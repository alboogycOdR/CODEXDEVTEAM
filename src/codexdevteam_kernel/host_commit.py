"""Host-owned, all-or-nothing commits for supervised task worktrees.

The maker never receives write access to shared Git metadata. This module reads
the worktree as untrusted input, validates a complete snapshot, builds a tree in
a temporary index and publishes one commit with an expected-old ref update.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .protocol import TaskRecord
from .secrets import find_secrets
from .territory import decide_write, matches_repo_path, normalize_repo_path


_HOST_NAME = "CODEXDEVTEAM Host"
_HOST_EMAIL = "host-committer@codexdevteam.invalid"
_INVOCATION_TRAILER = "CODEXDEVTEAM-Maker-Invocation"
_TASK_ID_RE = re.compile(r"^TASK-[A-Z0-9][A-Z0-9-]*$")
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                     *(f"LPT{i}" for i in range(1, 10))}


@dataclass(frozen=True, slots=True)
class QuiescenceProof:
    """Runtime evidence that no contained maker process can still write."""

    platform: str
    mechanism: str
    verified: bool
    active_after_exit: int
    detail: str = ""

    def __post_init__(self) -> None:
        if self.platform not in {"windows", "linux"}:
            raise ValueError("quiescence platform must be windows or linux")
        if self.mechanism not in {"windows_job_object", "linux_cgroup_v2",
                                  "linux_subreaper", "posix_process_group", "unknown"}:
            raise ValueError("unsupported quiescence mechanism")
        if not isinstance(self.verified, bool):
            raise ValueError("quiescence verified must be boolean")
        if (isinstance(self.active_after_exit, bool)
                or not isinstance(self.active_after_exit, int)
                or self.active_after_exit < 0):
            raise ValueError("active_after_exit must be a non-negative integer")
        if self.verified and self.active_after_exit != 0:
            raise ValueError("verified quiescence requires zero active processes")
        if self.verified and self.mechanism == "posix_process_group":
            raise ValueError("a bare process-group check cannot prove quiescence")
        if self.verified and self.platform == "windows" and self.mechanism != "windows_job_object":
            raise ValueError("verified Windows quiescence requires a Job Object")
        if self.verified and self.platform == "linux" and self.mechanism not in {
                "linux_cgroup_v2", "linux_subreaper"}:
            raise ValueError("verified Linux quiescence requires cgroup v2 or a subreaper")


@dataclass(frozen=True, slots=True)
class CommitLimits:
    max_file_bytes: int = 2 * 1024 * 1024
    settle_seconds: float = 2.0
    ignored_allowlist: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (isinstance(self.max_file_bytes, bool) or not isinstance(self.max_file_bytes, int)
                or self.max_file_bytes < 1):
            raise ValueError("max_file_bytes must be a positive integer")
        if (not isinstance(self.settle_seconds, (int, float))
                or isinstance(self.settle_seconds, bool) or self.settle_seconds < 0):
            raise ValueError("settle_seconds must be non-negative")
        if not isinstance(self.ignored_allowlist, tuple):
            raise ValueError("ignored_allowlist must be a tuple")
        for path in self.ignored_allowlist:
            normalize_repo_path(path)


@dataclass(frozen=True, slots=True)
class RefusalReason:
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class HostCommitResult:
    status: str
    sha: str | None = None
    reasons: tuple[RefusalReason, ...] = ()
    paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"committed", "no_changes", "refused"}:
            raise ValueError("invalid host commit result status")
        if (self.status == "committed") != (self.sha is not None):
            raise ValueError("only committed results carry a SHA")


def validate_owned_retry_worktree(repository: str | Path, worktree: str | Path,
                                  task: TaskRecord, *, task_branch: str,
                                  expected_parent: str,
                                  ignored_allowlist: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Revalidate an internally authorized retry worktree before launching a maker."""
    repo = Path(repository).expanduser().resolve()
    tree = Path(worktree).expanduser().resolve()
    git_dir = _trusted_git_dir(repo)
    admin = _verify_worktree_pointer(git_dir, tree, task_branch)
    _check_branch_name(git_dir, task_branch, task.task_id)
    _reject_external_filters(git_dir)
    core_options = _effective_core_options(git_dir)
    base = _read_parent_tree(git_dir, repo, expected_parent)
    limits = CommitLimits(ignored_allowlist=(".codexdevteam/control",
                                             *ignored_allowlist), settle_seconds=0)
    snapshot = _capture_snapshot(tree, base, task, admin, repo, expected_parent, limits,
                                 limits.ignored_allowlist, core_options)
    outside = tuple(path for path in snapshot.changed
                    if not decide_write(path, task.owned_paths).allowed)
    if outside:
        raise _Refused("OUTSIDE_TERRITORY", "retry worktree still contains out-of-scope paths",
                       *outside)
    # A second snapshot closes a race between the check and runtime launch.
    confirmed = _capture_snapshot(tree, base, task, admin, repo, expected_parent, limits,
                                  limits.ignored_allowlist, core_options)
    if _snapshot_signature(snapshot) != _snapshot_signature(confirmed):
        raise _Refused("LATE_WRITE_DETECTED", "retry worktree changed during validation",
                       *confirmed.changed)
    return confirmed.changed


def quarantine_out_of_scope_changes(repository: str | Path, worktree: str | Path,
                                    task: TaskRecord, *, task_branch: str,
                                    expected_parent: str, invocation_id: str,
                                    paths: tuple[str, ...],
                                    ignored_allowlist: tuple[str, ...] = ()) -> tuple[Path, tuple[str, ...]]:
    """Preserve and restore only refused out-of-scope files in host-owned Git metadata."""
    repo = Path(repository).expanduser().resolve()
    tree = Path(worktree).expanduser().resolve()
    git_dir = _trusted_git_dir(repo)
    admin = _verify_worktree_pointer(git_dir, tree, task_branch)
    _check_branch_name(git_dir, task_branch, task.task_id)
    base = _read_parent_tree(git_dir, repo, expected_parent)
    core_options = _effective_core_options(git_dir)
    limits = CommitLimits(ignored_allowlist=(".codexdevteam/control",
                                             *ignored_allowlist), settle_seconds=0)
    snapshot = _capture_snapshot(tree, base, task, admin, repo, expected_parent, limits,
                                 limits.ignored_allowlist, core_options)
    refused = tuple(sorted(set(paths)))
    if not refused or any(path not in snapshot.changed for path in refused):
        raise _Refused("QUARANTINE", "refused paths do not match the stable worktree snapshot",
                       *refused)
    if any(decide_write(path, task.owned_paths).allowed for path in refused):
        raise _Refused("QUARANTINE", "refusal paths include task-owned content", *refused)
    confirmed = _capture_snapshot(tree, base, task, admin, repo, expected_parent, limits,
                                  limits.ignored_allowlist, core_options)
    if _snapshot_signature(snapshot) != _snapshot_signature(confirmed):
        raise _Refused("QUIESCENCE_UNPROVEN", "worktree changed before quarantine", *refused)

    invocation_key = hashlib.sha256(invocation_id.encode("utf-8")).hexdigest()
    parent_dir = git_dir / "codexdevteam" / "quarantine" / task.task_id
    _ensure_host_directory(git_dir, parent_dir)
    quarantine = parent_dir / invocation_key
    quarantine.mkdir()
    payload_dir = quarantine / "files"
    payload_dir.mkdir()
    manifest: list[dict[str, object]] = []
    for index, relative in enumerate(refused):
        item = snapshot.files.get(relative)
        current_path = tree.joinpath(*PurePosixPath(relative).parts)
        exists = item is not None
        if exists:
            if item.data is None:
                raise _Refused("QUARANTINE", f"quarantine bytes missing: {relative}", relative)
            _validate_path_form(relative)
            data = item.data
            payload = payload_dir / f"{index:04d}.bin"
            _write_exclusive(payload, data)
            now = current_path.stat(follow_symlinks=False)
            signature = (now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns, now.st_mode)
            if signature != item.signature or hashlib.sha256(current_path.read_bytes()).hexdigest() != item.content_sha256:
                raise _Refused("QUIESCENCE_UNPROVEN", f"path changed while quarantining: {relative}", relative)
            current_sha = item.content_sha256
        else:
            data = b""
            current_sha = None
        original = base.get(relative)
        manifest.append({
            "path": relative, "existed": exists, "content_sha256": current_sha,
            "size": len(data) if exists else 0,
            "parent_mode": original[0] if original else None,
            "parent_blob": original[1] if original else None,
        })

    manifest_path = quarantine / "manifest.json"
    _write_exclusive(manifest_path,
                     (json.dumps({"task_id": task.task_id, "invocation_id": invocation_id,
                                  "expected_parent": expected_parent,
                                  "paths": manifest}, sort_keys=True, indent=2) + "\n").encode())

    for entry in manifest:
        relative = str(entry["path"])
        target = tree.joinpath(*PurePosixPath(relative).parts)
        _verify_no_link_components(tree, target)
        original = base.get(relative)
        if original is None:
            if target.exists():
                info = target.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or _is_reparse_or_link(target):
                    raise _Refused("QUARANTINE", f"cannot safely remove refused path: {relative}", relative)
                target.unlink()
                _remove_empty_parents(target.parent, tree)
            continue
        mode, blob = original
        if mode not in {0o100644, 0o100755}:
            raise _Refused("QUARANTINE", f"unsupported parent file mode for {relative}", relative)
        data = _git(git_dir, repo, "cat-file", "blob", blob).stdout
        _write_restored_file(tree, target, data, mode)

    remaining = _capture_snapshot(tree, base, task, admin, repo, expected_parent, limits,
                                  limits.ignored_allowlist, core_options)
    outside = tuple(path for path in remaining.changed
                    if not decide_write(path, task.owned_paths).allowed)
    if outside:
        raise _Refused("QUARANTINE", "out-of-scope paths remain after restore", *outside)
    return quarantine, tuple(sorted(entry["path"] for entry in manifest))


def archive_refused_control_outbox(repository: str | Path, worktree: str | Path,
                                   task: TaskRecord, *, task_branch: str,
                                   invocation_id: str, outbox: str | Path) -> tuple[Path, tuple[str, ...]]:
    """Move one invocation's CONTROL reports out of the maker-writable worktree."""
    repo = Path(repository).expanduser().resolve()
    tree = Path(worktree).expanduser().resolve()
    git_dir = _trusted_git_dir(repo)
    _verify_worktree_pointer(git_dir, tree, task_branch)
    source_dir = Path(outbox)
    if source_dir.is_symlink() or not source_dir.resolve(strict=True).is_relative_to(tree):
        raise _Refused("CONTROL_ARCHIVE", "CONTROL outbox is outside the verified worktree")
    if not source_dir.is_dir():
        raise _Refused("CONTROL_ARCHIVE", "CONTROL outbox is not a directory")
    unexpected = tuple(item.name for item in source_dir.iterdir()
                       if item.suffix.lower() != ".json")
    if unexpected:
        raise _Refused("CONTROL_ARCHIVE", "CONTROL outbox contains unexpected entries")
    invocation_key = hashlib.sha256(invocation_id.encode("utf-8")).hexdigest()
    archive_parent = git_dir / "codexdevteam" / "control-refused" / task.task_id
    _ensure_host_directory(git_dir, archive_parent)
    archive = archive_parent / invocation_key
    archive.mkdir()
    moved: list[str] = []
    digests: list[dict[str, str]] = []
    for source in sorted(source_dir.glob("*.json")):
        if source.is_symlink() or _is_reparse_or_link(source):
            raise _Refused("CONTROL_ARCHIVE", f"unsafe CONTROL report: {source.name}")
        info = source.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024:
            raise _Refused("CONTROL_ARCHIVE", f"invalid CONTROL report: {source.name}")
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        destination = archive / source.name
        _write_exclusive(destination, data)
        if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise _Refused("CONTROL_ARCHIVE", f"CONTROL archive hash mismatch: {source.name}")
        source.unlink()
        moved.append(source.name)
        digests.append({"name": source.name, "sha256": digest})
    _write_exclusive(archive / "manifest.json",
                     (json.dumps({"task_id": task.task_id, "invocation_id": invocation_id,
                                  "reports": digests}, sort_keys=True, indent=2) + "\n").encode())
    return archive, tuple(moved)


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    relative: str
    data: bytes | None
    mode: int
    signature: tuple[int, int, int, int, int]
    content_sha256: str


@dataclass(frozen=True, slots=True)
class _CapturedSnapshot:
    files: dict[str, _FileSnapshot]
    changed: tuple[str, ...]
    ignored: tuple[str, ...]


class _Refused(Exception):
    def __init__(self, code: str, detail: str, *paths: str):
        super().__init__(detail)
        self.reason = RefusalReason(code, detail)
        self.paths = tuple(paths)


def host_commit(repository: str | Path, worktree: str | Path, task: TaskRecord, *,
                task_branch: str, expected_parent: str, invocation_id: str,
                quiescence: QuiescenceProof, limits: CommitLimits = CommitLimits()) -> HostCommitResult:
    """Validate and commit one complete worktree snapshot, or refuse atomically.

    All Git commands use the trusted repository's common Git directory and an
    explicit worktree path. The worktree's own ``.git`` pointer is checked
    against the trusted repository's worktree registry before any worktree
    content is read through Git.
    """
    try:
        if not isinstance(task, TaskRecord):
            raise _Refused("INPUT", "task must be a validated TaskRecord")
        if not isinstance(quiescence, QuiescenceProof):
            raise _Refused("INPUT", "quiescence must be a QuiescenceProof")
        if not isinstance(limits, CommitLimits):
            raise _Refused("INPUT", "limits must be CommitLimits")
        repo = Path(repository).expanduser().resolve()
        tree = Path(worktree).expanduser().resolve()
        if quiescence.verified is not True or quiescence.active_after_exit != 0:
            raise _Refused("QUIESCENCE_UNPROVEN", "maker process quiescence is not verified")
        if not _TASK_ID_RE.fullmatch(task.task_id):
            raise _Refused("TASK_ID", "task ID is malformed")
        if (not isinstance(invocation_id, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", invocation_id)):
            raise _Refused("INVOCATION_ID", "maker invocation ID is malformed")
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", expected_parent or ""):
            raise _Refused("PARENT", "expected parent must be a full Git object ID")
        git_dir = _trusted_git_dir(repo)
        core_options = _effective_core_options(git_dir)
        _reject_external_filters(git_dir)
        _check_branch_name(git_dir, task_branch, task.task_id)
        worktree_admin = _verify_worktree_pointer(git_dir, tree, task_branch)
        parent = expected_parent.lower()
        current = _git(git_dir, repo, "rev-parse", "--verify", f"refs/heads/{task_branch}").stdout.decode().strip()
        if current.lower() != parent:
            raise _Refused("PARENT_MOVED", "task branch no longer points at the recorded parent")
        _git(git_dir, repo, "cat-file", "-e", f"{parent}^{{commit}}")

        base = _read_parent_tree(git_dir, repo, parent)
        gitlinks = {path for path, (mode, _) in base.items() if mode == 0o160000}
        allowlist = tuple(limits.ignored_allowlist)
        # Status must resolve HEAD from the linked worktree's private git dir.
        # The common git dir's HEAD belongs to the primary checkout, which can
        # differ from this task's parent and make unchanged task files appear
        # as staged additions/deletions in the temporary index.
        first = _capture_snapshot(tree, base, task, worktree_admin, repo, parent, limits,
                                  allowlist, core_options)
        if limits.settle_seconds:
            time.sleep(limits.settle_seconds)
        second = _capture_snapshot(tree, base, task, worktree_admin, repo, parent, limits,
                                   allowlist, core_options)
        if _snapshot_signature(first) != _snapshot_signature(second):
            raise _Refused("QUIESCENCE_UNPROVEN", "worktree changed during the settle interval")
        snapshot = second
        changed = snapshot.changed
        for path in changed:
            _validate_path_form(path)
        affected_gitlinks = tuple(path for path in changed
                                  if any(path == link or path.startswith(link + "/")
                                         for link in gitlinks))
        if affected_gitlinks:
            raise _Refused("GITLINK", "changes under submodules are not supported",
                           *affected_gitlinks)
        _check_case_aliases(changed, tuple(snapshot.files), base)
        if not changed:
            return HostCommitResult("no_changes")

        # Refuse as a whole before writing any Git object.
        offenders: list[str] = []
        for path in changed:
            try:
                decision = decide_write(path, task.owned_paths)
            except ValueError:
                raise _Refused("PATH_FORM", f"unsafe repository path: {path}", path)
            if not decision.allowed:
                offenders.append(path)
        if offenders:
            raise _Refused("OUTSIDE_TERRITORY", "changed paths exceed task ownership",
                           *sorted(offenders))

        # Validate changed bytes and metadata before writing blobs.
        for path in changed:
            item = snapshot.files.get(path)
            if item is None:  # A deletion is validated by ownership above.
                continue
            _validate_path_form(path)
            _validate_file(tree, item, limits)
            if find_secrets(item.data.decode("utf-8", errors="replace")):
                raise _Refused("SECRET", f"secret pattern detected in {path}", path)
            ignored = _git(git_dir, tree, "check-ignore", "--no-index", "-q", "--", path,
                           check=False)
            if ignored.returncode == 0 and not _allowed_ignored(path, allowlist):
                raise _Refused("IGNORED_PATH", f"ignored path is not allow-listed: {path}", path)
            if ignored.returncode not in {0, 1}:
                raise _Refused("GIT_STATUS", f"could not inspect ignore rules for {path}", path)

        with tempfile.TemporaryDirectory(prefix="codexdevteam-host-commit-") as temp_dir:
            index_path = Path(temp_dir) / "index"
            env = _git_env(index_file=index_path)
            _git(git_dir, tree, "read-tree", parent, env=env)
            for path in changed:
                item = snapshot.files.get(path)
                if item is None:
                    _git(git_dir, tree, "update-index", "--force-remove", "--", path, env=env,
                         check=False)
                    continue
                if item.data is None:
                    raise _Refused("SNAPSHOT", f"changed file bytes were not captured: {path}", path)
                blob = _git(git_dir, tree, "hash-object", "-w", "--stdin",
                            input=item.data).stdout.decode().strip()
                _git(git_dir, tree, "update-index", "--add", "--cacheinfo",
                     f"{item.mode:o}", blob, path, env=env)
            tree_sha = _git(git_dir, tree, "write-tree", env=env).stdout.decode().strip()
            message = (f"Complete task [{task.task_id}]\n\n"
                       f"{_INVOCATION_TRAILER}: {invocation_id}\n")
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            commit_env = _git_env(extra={"GIT_AUTHOR_DATE": now, "GIT_COMMITTER_DATE": now})
            commit_sha = _git(
                git_dir, tree, "-c", f"user.name={_HOST_NAME}", "-c",
                f"user.email={_HOST_EMAIL}", "-c", "commit.gpgsign=false",
                "commit-tree", tree_sha, "-p", parent, input=message.encode("utf-8"),
                env=commit_env,
            ).stdout.decode().strip()

            _verify_worktree_index(git_dir, tree, worktree_admin, parent)
            index_lock = worktree_admin / "index.lock"
            actual_index = worktree_admin / "index"
            try:
                with index_lock.open("xb") as locked:
                    locked.write(index_path.read_bytes())
                    locked.flush()
                    os.fsync(locked.fileno())
            except FileExistsError as exc:
                raise _Refused("INDEX_LOCKED", "task worktree index is locked") from exc

            published = False
            try:
                # Final tripwire: compare the complete worktree snapshot to
                # the validated bytes immediately before ref publication.
                final = _capture_snapshot(tree, base, task, worktree_admin, repo, parent, limits,
                                          allowlist, core_options)
                if _snapshot_signature(final) != _snapshot_signature(snapshot):
                    raise _Refused("LATE_WRITE_DETECTED", "worktree changed after validation",
                                   *final.changed)
                publish = _git(git_dir, repo, "update-ref", f"refs/heads/{task_branch}",
                               commit_sha, parent, check=False)
                if publish.returncode:
                    current = _git(git_dir, repo, "rev-parse", "--verify",
                                   f"refs/heads/{task_branch}", check=False)
                    code = ("PARENT_MOVED" if current.returncode == 0
                            and current.stdout.decode().strip().lower() != parent
                            else "PUBLISH_FAILED")
                    raise _Refused(code, "atomic task branch publication failed")
                published = True
                os.replace(index_lock, actual_index)
            except OSError as exc:
                if published:
                    index_lock.unlink(missing_ok=True)
                    return HostCommitResult(
                        "committed", sha=commit_sha, paths=tuple(sorted(changed)),
                        reasons=(RefusalReason("INDEX_REFRESH_FAILED",
                                               f"commit published but worktree index refresh failed: {exc}"),),
                    )
                raise
            finally:
                if not published:
                    index_lock.unlink(missing_ok=True)
        return HostCommitResult("committed", sha=commit_sha,
                                paths=tuple(sorted(changed)))
    except _Refused as exc:
        return HostCommitResult("refused", reasons=(exc.reason,), paths=exc.paths)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError) as exc:
        return HostCommitResult("refused", reasons=(RefusalReason(
            "HOST_COMMIT_ERROR", f"host commit could not be safely completed: {exc}"),))


def _trusted_git_dir(repository: Path) -> Path:
    result = subprocess.run(["git", "-C", str(repository), "rev-parse", "--git-common-dir"],
                            capture_output=True, env=_git_env())
    if result.returncode:
        raise _Refused("REPOSITORY", "trusted repository Git directory could not be resolved")
    raw = Path(os.fsdecode(result.stdout.strip()))
    common = (repository / raw).resolve() if not raw.is_absolute() else raw.resolve()
    if not common.is_dir():
        raise _Refused("REPOSITORY", "trusted Git common directory is missing")
    return common


def _check_branch_name(git_dir: Path, branch: str, task_id: str) -> None:
    if not isinstance(branch, str) or not branch or branch.startswith("-"):
        raise _Refused("BRANCH", "task branch name is malformed")
    if task_id.casefold() not in {part.casefold() for part in branch.split("/")}:
        raise _Refused("BRANCH", "branch name does not identify this task")
    result = _git(git_dir, git_dir, "check-ref-format", f"refs/heads/{branch}", check=False)
    if result.returncode:
        raise _Refused("BRANCH", "task branch name is not a valid local branch")


def _reject_external_filters(git_dir: Path) -> None:
    result = _git(git_dir, git_dir, "config", "--name-only", "--get-regexp",
                  r"^filter\..*\.(clean|process)$", check=False)
    if result.returncode == 0:
        raise _Refused("UNSAFE_GIT_CONFIG", "configured Git clean filters are not allowed")
    if result.returncode != 1:
        raise _Refused("UNSAFE_GIT_CONFIG", "could not verify Git filter configuration")


def _effective_core_options(git_dir: Path) -> tuple[tuple[str, str], ...]:
    # Preserve line-ending semantics needed to compare a checked-out worktree,
    # while the commit path itself never invokes smudge/clean filters.
    options: list[tuple[str, str]] = []
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("GIT_")}
    for key, allowed in {
        "core.autocrlf": {"true", "false", "input", "auto"},
        "core.eol": {"lf", "crlf", "native"},
    }.items():
        result = subprocess.run(["git", f"--git-dir={git_dir}", "config", "--get", key],
                                capture_output=True, env=env)
        if result.returncode == 1:
            continue
        if result.returncode:
            raise _Refused("UNSAFE_GIT_CONFIG", f"could not inspect {key}")
        value = result.stdout.decode("utf-8", errors="strict").strip().lower()
        if value not in allowed:
            raise _Refused("UNSAFE_GIT_CONFIG", f"unsupported value for {key}")
        options.append((key, value))
    return tuple(options)


def _verify_worktree_pointer(git_dir: Path, worktree: Path, task_branch: str) -> Path:
    pointer = worktree / ".git"
    try:
        pointer_info = pointer.lstat()
        if (not stat.S_ISREG(pointer_info.st_mode) or pointer_info.st_nlink != 1
                or _is_reparse_or_link(pointer)):
            raise _Refused("GITFILE_TAMPERED", "worktree .git pointer is missing or not a file")
        actual = pointer.read_bytes()
        registered = _git(git_dir, git_dir, "worktree", "list", "--porcelain", "-z").stdout
        # Git's -z worktree output consists of NUL-delimited key/value records.
        entry: dict[str, str] = {}
        entries: list[dict[str, str]] = []
        for record in registered.split(b"\0"):
            if not record:
                if entry:
                    entries.append(entry)
                    entry = {}
                continue
            key, _, value = os.fsdecode(record).partition(" ")
            entry[key] = value
        if entry:
            entries.append(entry)
        matched = next((row for row in entries
                        if Path(row.get("worktree", "")).resolve() == worktree.resolve()), None)
        if matched is None or matched.get("branch") != f"refs/heads/{task_branch}":
            raise _Refused("GITFILE_TAMPERED", "worktree is not registered in the trusted repository")
        admin_root = git_dir / "worktrees"
        expected: list[Path] = []
        if admin_root.is_dir():
            for admin in admin_root.iterdir():
                gitdir_file = admin / "gitdir"
                if not gitdir_file.is_file():
                    continue
                try:
                    recorded = Path(os.fsdecode(gitdir_file.read_bytes().strip())).resolve()
                except (OSError, ValueError):
                    continue
                if recorded == pointer.resolve():
                    expected.append(admin.resolve())
        try:
            decoded = actual.decode("utf-8", errors="strict").strip()
            prefix, value = decoded.split(":", 1)
            if prefix != "gitdir":
                raise ValueError("invalid gitdir pointer")
            actual_admin = Path(value.strip())
            if not actual_admin.is_absolute():
                actual_admin = worktree / actual_admin
            actual_admin = actual_admin.resolve()
        except (UnicodeDecodeError, ValueError):
            actual_admin = Path("<invalid>")
        if len(expected) != 1 or actual_admin != expected[0]:
            raise _Refused("GITFILE_TAMPERED", "worktree .git pointer differs from trusted registration")
        return expected[0]
    except _Refused:
        raise
    except OSError as exc:
        raise _Refused("GITFILE_TAMPERED", f"cannot verify worktree .git pointer: {exc}") from exc


def _git(git_dir: Path, worktree: Path, *args: str, env: dict[str, str] | None = None,
         input: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess:
    command = ["git", f"--git-dir={git_dir}", f"--work-tree={worktree}", *args]
    result = subprocess.run(command, input=input, capture_output=True, env=env or _git_env())
    if check and result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise _Refused("GIT_COMMAND", detail or f"git {args[0]} failed")
    return result


def _git_env(*, index_file: Path | None = None,
             extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("GIT_")}
    for key in ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"):
        if key in os.environ:
            env[key] = os.environ[key]
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    if index_file is not None:
        env["GIT_INDEX_FILE"] = str(index_file)
    if extra:
        env.update(extra)
    return env


def _read_parent_tree(git_dir: Path, repository: Path, parent: str) -> dict[str, tuple[int, str]]:
    raw = _git(git_dir, repository, "ls-tree", "-r", "-z", "--full-tree", parent).stdout
    result: dict[str, tuple[int, str]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, path_bytes = record.split(b"\t", 1)
        mode_raw, kind, sha = metadata.split(b" ", 2)
        try:
            path = path_bytes.decode("utf-8", errors="strict")
            mode = int(mode_raw, 8)
        except (UnicodeDecodeError, ValueError) as exc:
            raise _Refused("PATH_FORM", "parent contains a non-UTF-8 path") from exc
        if mode == 0o160000 and kind == b"commit":
            result[path] = (mode, sha.decode("ascii"))
            continue
        if kind != b"blob":
            raise _Refused("GITLINK", f"parent contains an unsupported gitlink: {path}", path)
        result[path] = (mode, sha.decode("ascii"))
    return result


def _capture_snapshot(worktree: Path, base: dict[str, tuple[int, str]], task: TaskRecord,
                      git_dir: Path, repository: Path, parent: str, limits: CommitLimits,
                      allowlist: tuple[str, ...],
                      core_options: tuple[tuple[str, str], ...]) -> _CapturedSnapshot:
    try:
        with tempfile.TemporaryDirectory(prefix="codexdevteam-status-") as temp_dir:
            index_path = Path(temp_dir) / "index"
            status_env = _git_env(index_file=index_path)
            _git(git_dir, worktree, "read-tree", parent, env=status_env)
            status_args = ["-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false"]
            # Windows has no Git-compatible executable permission bit. Compare
            # bytes here and preserve trusted parent modes below, regardless of
            # builder-writable core.filemode or the builder index.
            if os.name == "nt":
                status_args.extend(("-c", "core.filemode=false"))
            for key, value in core_options:
                status_args.extend(("-c", f"{key}={value}"))
            status = _git(git_dir, worktree, *status_args, "status", "--porcelain=v2", "-z",
                          "--untracked-files=all", "--ignored=matching", "--no-renames",
                          env=status_env)
        parsed_changed, ignored_paths = _parse_status_paths(status.stdout)
        changed_paths = {path for path in parsed_changed
                         if not (_allowed_ignored(path.rstrip("/"), allowlist)
                                 and path not in base)}
        unapproved_ignored = tuple(path for path in ignored_paths
                                   if not _allowed_ignored(path.rstrip("/"), allowlist))
        if unapproved_ignored:
            raise _Refused("IGNORED_PATH", "ignored path is not allow-listed",
                           *unapproved_ignored)
        files: dict[str, _FileSnapshot] = {}
        for root, dirs, names in os.walk(worktree, topdown=True, followlinks=False):
            root_path = Path(root)
            dirs[:] = [name for name in dirs if not (root_path == worktree and name == ".git")]
            for name in list(dirs):
                path = root_path / name
                relative = path.relative_to(worktree).as_posix()
                if relative in {entry for entry, (mode, _) in base.items()
                                if mode == 0o160000}:
                    dirs.remove(name)
                    continue
                if _is_reparse_or_link(path):
                    # Do not traverse it. If it overlaps task scope, fail closed.
                    relative = path.relative_to(worktree).as_posix()
                    if relative in changed_paths or relative not in base:
                        raise _Refused("LINK", f"directory link or reparse point: {relative}", relative)
                    dirs.remove(name)
                    continue
                # Explicitly ignored generated trees may contain package-manager
                # junctions. Only prune a real directory that Git itself reports
                # ignored, and never hide tracked descendants or a linked root.
                if (relative + "/" in ignored_paths
                        and _allowed_ignored(relative, allowlist)
                        and not any(entry == relative or entry.startswith(relative + "/")
                                    for entry in base)):
                    dirs.remove(name)
            for name in names:
                path = root_path / name
                relative = path.relative_to(worktree).as_posix()
                if relative == ".git":
                    continue
                if _allowed_ignored(relative, allowlist) and relative not in base:
                    continue
                if _is_reparse_or_link(path):
                    if relative in changed_paths or relative not in base:
                        raise _Refused("LINK", f"link or reparse point: {relative}", relative)
                    continue
                if _has_alternate_stream(path):
                    raise _Refused("PATH_FORM", f"alternate data stream is refused: {relative}", relative)
                try:
                    info = path.stat(follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode):
                        raise _Refused("PATH_FORM", f"changed path is not a regular file: {relative}", relative)
                    if os.name == "nt":
                        parent_mode = base.get(relative, (0o100644, ""))[0]
                        mode = parent_mode if parent_mode in {0o100644, 0o100755} else 0o100644
                    else:
                        mode = 0o100755 if info.st_mode & 0o111 else 0o100644
                    is_changed = (relative in changed_paths or relative not in base
                                  or (relative in base and base[relative][0] != mode))
                    if is_changed and info.st_size > limits.max_file_bytes:
                        raise _Refused("SIZE", f"file exceeds {limits.max_file_bytes} byte limit: {relative}",
                                       relative)
                    if is_changed:
                        data = path.read_bytes()
                        content_sha256 = hashlib.sha256(data).hexdigest()
                    else:
                        data = None
                        digest = hashlib.sha256()
                        with path.open("rb") as stream:
                            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                                digest.update(chunk)
                        content_sha256 = digest.hexdigest()
                except _Refused:
                    raise
                except OSError as exc:
                    raise _Refused("FILE_READ", f"cannot read {relative}: {exc}", relative) from exc
                signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode)
                files[relative] = _FileSnapshot(relative, data, mode, signature, content_sha256)
        # Git may not report a mode change when core.filemode is disabled. Add
        # POSIX executable-bit changes from the filesystem walk explicitly.
        changed_paths.update(path for path, item in files.items()
                             if path in base and base[path][0] != item.mode)
        return _CapturedSnapshot(files, tuple(sorted(changed_paths)),
                                 tuple(sorted(ignored_paths)))
    except _Refused:
        raise
    except (OSError, UnicodeError) as exc:
        raise _Refused("SNAPSHOT", f"cannot snapshot worktree: {exc}") from exc


def _parse_status_paths(raw: bytes) -> tuple[tuple[str, ...], tuple[str, ...]]:
    paths: list[str] = []
    ignored: list[str] = []
    for record in raw.split(b"\0"):
        if not record or record.startswith(b"# "):
            continue
        kind = record[:2]
        if kind in {b"? ", b"! "}:
            path_bytes = record[2:]
        elif record.startswith(b"u "):
            raise _Refused("UNMERGED", "unmerged paths cannot be host-committed")
        elif record.startswith(b"1 "):
            fields = record.split(b" ", 8)
            if len(fields) != 9:
                raise _Refused("GIT_STATUS", "malformed porcelain-v2 status record")
            path_bytes = fields[8]
        elif record.startswith(b"2 "):
            fields = record.split(b" ", 9)
            if len(fields) != 10:
                raise _Refused("GIT_STATUS", "malformed rename status record")
            path_bytes = fields[9]
        else:
            raise _Refused("GIT_STATUS", "unsupported porcelain-v2 status record")
        try:
            path = path_bytes.decode("utf-8", errors="strict")
            if kind == b"! ":
                ignored.append(path)
            else:
                paths.append(path)
        except UnicodeDecodeError as exc:
            raise _Refused("PATH_FORM", "worktree contains a non-UTF-8 path") from exc
    return tuple(paths), tuple(ignored)


def _snapshot_signature(snapshot: _CapturedSnapshot) -> tuple:
    return (tuple(sorted((path, item.content_sha256, item.mode,
                          item.signature) for path, item in snapshot.files.items())),
            snapshot.changed, snapshot.ignored)


def _ensure_host_directory(root: Path, target: Path) -> None:
    root = root.resolve(strict=True)
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise _Refused("QUARANTINE", "host archive path escapes Git metadata") from exc
    current = root
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            current.mkdir()
            info = current.lstat()
        if (not stat.S_ISDIR(info.st_mode) or _is_reparse_or_link(current)):
            raise _Refused("QUARANTINE", "host archive directory is not a real directory")


def _write_exclusive(path: Path, data: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise _Refused("QUARANTINE", f"host archive file already exists: {path.name}") from exc


def _verify_no_link_components(root: Path, target: Path) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise _Refused("QUARANTINE", "worktree path escapes its root") from exc
    current = root
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if _is_reparse_or_link(current):
            raise _Refused("QUARANTINE", f"link or reparse point blocks restore: {current.name}")
        if current != target and not stat.S_ISDIR(info.st_mode):
            raise _Refused("QUARANTINE", f"non-directory path component blocks restore: {current.name}")


def _write_restored_file(root: Path, target: Path, data: bytes, mode: int) -> None:
    _verify_no_link_components(root, target.parent)
    target.parent.mkdir(parents=True, exist_ok=True)
    _verify_no_link_components(root, target)
    temporary = target.with_name(f".{target.name}.codexdevteam-restore-{os.urandom(8).hex()}")
    _write_exclusive(temporary, data)
    try:
        os.chmod(temporary, 0o755 if mode == 0o100755 else 0o644)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_empty_parents(parent: Path, root: Path) -> None:
    while parent != root and parent.is_relative_to(root):
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent


def _validate_file(worktree: Path, item: _FileSnapshot, limits: CommitLimits) -> None:
    if len(item.data) > limits.max_file_bytes:
        raise _Refused("SIZE", f"file exceeds {limits.max_file_bytes} byte limit: {item.relative}",
                       item.relative)
    path = worktree.joinpath(*PurePosixPath(item.relative).parts)
    try:
        info = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise _Refused("LATE_WRITE_DETECTED", f"file disappeared during validation: {item.relative}",
                       item.relative) from exc
    if info.st_nlink > 1:
        raise _Refused("HARDLINK", f"hard-linked file is refused: {item.relative}", item.relative)
    signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode)
    if signature != item.signature:
        raise _Refused("LATE_WRITE_DETECTED", f"file changed during validation: {item.relative}",
                       item.relative)


def _verify_worktree_index(git_dir: Path, worktree: Path, admin: Path,
                           parent: str) -> None:
    index = admin / "index"
    if not index.is_file():
        raise _Refused("INDEX_TAMPERED", "task worktree index is missing")
    result = _git(git_dir, worktree, "write-tree",
                  env=_git_env(index_file=index), check=False)
    parent_tree = _git(git_dir, worktree, "rev-parse", f"{parent}^{{tree}}").stdout.decode().strip()
    if result.returncode or result.stdout.decode().strip() != parent_tree:
        raise _Refused("INDEX_TAMPERED", "task worktree index differs from expected parent")


def _validate_path_form(path: str) -> None:
    try:
        normalized = normalize_repo_path(path)
    except ValueError as exc:
        raise _Refused("PATH_FORM", f"unsafe repository path: {path}", path) from exc
    if normalized != path or path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        raise _Refused("PATH_FORM", f"non-canonical repository path: {path}", path)
    for part in path.split("/"):
        if part.casefold() == ".git":
            raise _Refused("PATH_FORM", f"nested Git metadata path is refused: {path}", path)
        if re.search(r"~[0-9]+(?:\.|$)", part):
            raise _Refused("PATH_FORM", f"8.3 short-name path is refused: {path}", path)
        if ":" in part or part.endswith((".", " ")):
            raise _Refused("PATH_FORM", f"Windows path alias is refused: {path}", path)
        stem = part.split(".", 1)[0].upper()
        if stem in _WINDOWS_RESERVED:
            raise _Refused("PATH_FORM", f"reserved Windows device path: {path}", path)


def _allowed_ignored(path: str, allowlist: tuple[str, ...]) -> bool:
    return any(path == item or path.startswith(item.rstrip("/") + "/")
               or matches_repo_path(path, item) for item in allowlist)


def _check_case_aliases(changed: tuple[str, ...], observed_paths: tuple[str, ...],
                        base: dict[str, tuple[int, str]]) -> None:
    by_fold = {path.casefold(): path for path in base}
    for path in observed_paths:
        existing = by_fold.get(path.casefold())
        if existing is not None and existing != path:
            raise _Refused("PATH_FORM", f"case-folded path alias conflicts with {existing}", path,
                           existing)


def _is_reparse_or_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    attrs = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(attrs & reparse)


def _has_alternate_stream(path: Path) -> bool:
    if os.name != "nt":
        return False

    class _FindStreamData(ctypes.Structure):
        _fields_ = [("StreamSize", ctypes.c_longlong), ("cStreamName", wintypes.WCHAR * 296)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.FindFirstStreamW.argtypes = (wintypes.LPCWSTR, ctypes.c_int,
                                         ctypes.POINTER(_FindStreamData), wintypes.DWORD)
    kernel32.FindFirstStreamW.restype = wintypes.HANDLE
    kernel32.FindNextStreamW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_FindStreamData))
    kernel32.FindNextStreamW.restype = wintypes.BOOL
    kernel32.FindClose.argtypes = (wintypes.HANDLE,)
    kernel32.FindClose.restype = wintypes.BOOL
    data = _FindStreamData()
    handle = kernel32.FindFirstStreamW(str(path), 0, ctypes.byref(data), 0)
    if handle == wintypes.HANDLE(-1).value:
        error = ctypes.get_last_error()
        if error in {2, 3, 38}:
            return False
        raise _Refused("PATH_FORM", f"cannot inspect Windows data streams: {path}",
                       path.name)
    try:
        while True:
            if data.cStreamName not in {"::$DATA", ":$DATA"}:
                return True
            if not kernel32.FindNextStreamW(handle, ctypes.byref(data)):
                return False
    finally:
        kernel32.FindClose(handle)
