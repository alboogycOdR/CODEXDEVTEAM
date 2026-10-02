"""Repository-bound Git worktree lifecycle primitives."""

import re
import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class WorktreeError(RuntimeError):
    """Raised when a worktree operation cannot be proven safe."""


@dataclass(frozen=True, slots=True)
class WorktreeInfo:
    path: Path
    branch: str | None
    head: str


class GitWorktreeManager:
    """Manage task worktrees inside one explicitly owned directory."""

    def __init__(self, repository: str | Path, managed_root: str | Path | None = None):
        self.repository = self._git_root(Path(repository))
        root = Path(managed_root) if managed_root else (
            self.repository.parent / f"{self.repository.name}-codexdevteam-worktrees"
        )
        self.managed_root = root.expanduser().resolve()
        if self.managed_root == self.repository or self.repository in self.managed_root.parents:
            raise ValueError("managed_root must be outside the repository")

    def create(self, task_id: str, branch: str, base_ref: str, *,
               path: str | Path | None = None,
               worktree_copy: tuple[str, ...] = ()) -> WorktreeInfo:
        if not re.fullmatch(r"TASK-[A-Z0-9][A-Z0-9-]*", task_id):
            raise ValueError("invalid task_id")
        if not branch.strip() or not base_ref.strip():
            raise ValueError("branch and base_ref are required")
        target = Path(path).expanduser().resolve() if path else (self.managed_root / task_id).resolve()
        self._require_managed(target)
        registered = self._registered()
        reused = False
        if target.exists():
            current = registered.get(self._key(target))
            if current and current.branch == branch:
                created = current
                reused = True
            else:
                raise WorktreeError(f"refusing to reuse existing unverified path: {target}")
        else:
            if self._branch_exists(branch):
                self._run("worktree", "add", str(target), branch)
            else:
                self._run("worktree", "add", "-b", branch, str(target), base_ref)
            created = self._registered().get(self._key(target))
        if not created or created.branch != branch:
            raise WorktreeError("Git created a worktree but ownership verification failed")
        if self._git_common_dir(target) != self._git_common_dir(self.repository):
            raise WorktreeError("created worktree belongs to a different Git repository")
        if worktree_copy:
            try:
                self.copy_worktree_files(target, worktree_copy)
            except Exception as exc:
                if not reused:
                    try:
                        self._run("worktree", "remove", str(target))
                    except WorktreeError as cleanup_error:
                        raise WorktreeError(
                            f"worktree copy failed ({exc}); new worktree retained at {target}; "
                            f"cleanup failed ({cleanup_error})") from exc
                raise
        return created

    def inspect(self, path: str | Path) -> WorktreeInfo:
        target = Path(path).expanduser().resolve()
        self._require_managed(target)
        info = self._registered().get(self._key(target))
        if info is None:
            raise WorktreeError(f"path is not a registered worktree of this repository: {target}")
        return info

    def remove(self, path: str | Path, *, worktree_copy: tuple[str, ...] = ()) -> None:
        target = Path(path).expanduser().resolve()
        self._require_managed(target)
        info = self._registered().get(self._key(target))
        if info is None:
            raise WorktreeError(f"refusing to remove unregistered path: {target}")
        if target == self.repository:
            raise WorktreeError("refusing to remove the primary checkout")
        # Deliberately no --force: dirty/untracked work must be preserved for review.
        dirty = self._run("-C", str(target), "status", "--porcelain", "--untracked-files=all").strip()
        if dirty:
            raise WorktreeError("refusing to remove a dirty worktree")
        removed_files = self.remove_worktree_files(target, worktree_copy)
        try:
            self._run("worktree", "remove", str(target))
        except WorktreeError:
            self._restore_removed_files(target, removed_files)
            raise

    def copy_worktree_files(self, path: str | Path, relative_paths: tuple[str, ...]) -> tuple[str, ...]:
        """Refresh configured files from the primary checkout after proving they are ignored."""
        target = self.inspect(path).path
        staged: list[tuple[Path, Path, bytes | None, int]] = []
        committed: list[tuple[Path, bytes | None, int]] = []
        try:
            for relative in self._validate_copy_paths(relative_paths):
                source = self.repository.joinpath(*PurePosixPath(relative).parts)
                destination = target.joinpath(*PurePosixPath(relative).parts)
                if self._has_symlink(self.repository, source):
                    raise WorktreeError(f"configured copy source contains a symlink: {relative}")
                if not source.is_file():
                    raise WorktreeError(f"configured copy source is missing or not a file: {relative}")
                if self._has_symlink(target, destination):
                    raise WorktreeError(f"configured copy destination contains a symlink: {relative}")
                ignored = subprocess.run(["git", "-C", str(target), "check-ignore", "--no-index",
                                          "-q", "--", relative], capture_output=True)
                if ignored.returncode != 0:
                    if ignored.returncode == 1:
                        raise WorktreeError(f"configured worktree copy is not gitignored: {relative}")
                    raise WorktreeError(f"could not verify gitignore rule for: {relative}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                previous = destination.read_bytes() if destination.exists() else None
                if destination.exists() and not destination.is_file():
                    raise WorktreeError(f"configured copy destination is not a file: {relative}")
                mode = stat.S_IMODE(source.stat().st_mode)
                fd, temp_name = tempfile.mkstemp(prefix=".codexdevteam-copy-",
                                                 dir=destination.parent)
                os.close(fd)
                temp_path = Path(temp_name)
                temp_path.write_bytes(source.read_bytes())
                os.chmod(temp_path, mode)
                staged.append((destination, temp_path, previous, mode))
            for destination, temp_path, previous, mode in staged:
                if self._has_symlink(target, destination):
                    raise WorktreeError(f"configured copy destination became a symlink: {destination}")
                os.replace(temp_path, destination)
                committed.append((destination, previous, mode))
        except Exception:
            self._rollback_copy(target, committed)
            raise
        finally:
            for _, temp_path, _, _ in staged:
                temp_path.unlink(missing_ok=True)
        return tuple(relative_paths)

    def remove_worktree_files(self, path: str | Path,
                              relative_paths: tuple[str, ...]) -> dict[str, tuple[bytes, int]]:
        """Remove only declared, ignored resource copies and return rollback data."""
        target = self.inspect(path).path
        removed: dict[str, tuple[bytes, int]] = {}
        validated = self._validate_copy_paths(relative_paths)
        for relative in validated:
            destination = target.joinpath(*PurePosixPath(relative).parts)
            if self._has_symlink(target, destination):
                raise WorktreeError(f"configured copy destination contains a symlink: {relative}")
            if not destination.exists():
                continue
            ignored = subprocess.run(["git", "-C", str(target), "check-ignore", "--no-index",
                                      "-q", "--", relative], capture_output=True)
            if ignored.returncode != 0:
                raise WorktreeError(f"refusing to delete a copy that is no longer gitignored: {relative}")
            if not destination.is_file():
                raise WorktreeError(f"configured copy destination is not a file: {relative}")
            removed[relative] = (destination.read_bytes(), stat.S_IMODE(destination.stat().st_mode))
        for relative in removed:
            target.joinpath(*PurePosixPath(relative).parts).unlink()
        return removed

    def _rollback_copy(self, target: Path,
                       committed: list[tuple[Path, bytes | None, int]]) -> None:
        for destination, previous, mode in reversed(committed):
            if previous is None:
                destination.unlink(missing_ok=True)
            else:
                destination.write_bytes(previous)
                os.chmod(destination, mode)

    def _restore_removed_files(self, target: Path, removed: dict[str, tuple[bytes, int]]) -> None:
        for relative, (content, mode) in removed.items():
            destination = target.joinpath(*PurePosixPath(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            os.chmod(destination, mode)

    @staticmethod
    def _validate_copy_paths(relative_paths: tuple[str, ...]) -> tuple[str, ...]:
        if not isinstance(relative_paths, tuple):
            raise WorktreeError("worktree_copy must be a tuple of relative POSIX paths")
        result: list[str] = []
        for relative in relative_paths:
            if not isinstance(relative, str) or "\\" in relative:
                raise WorktreeError("worktree_copy paths must use relative POSIX form")
            parsed = PurePosixPath(relative)
            if (parsed.is_absolute() or not parsed.parts
                    or any(part in {"", ".", ".."} for part in parsed.parts)
                    or parsed.parts[0] == ".git"):
                raise WorktreeError(f"unsafe worktree_copy path: {relative}")
            if relative in result:
                raise WorktreeError(f"duplicate worktree_copy path: {relative}")
            result.append(relative)
        return tuple(result)

    @staticmethod
    def _has_symlink(root: Path, path: Path) -> bool:
        try:
            relative = path.relative_to(root)
        except ValueError:
            return True
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return True
        return False

    def _registered(self) -> dict[str, WorktreeInfo]:
        result = self._run("worktree", "list", "--porcelain")
        entries: list[dict[str, str]] = []
        for line in result.splitlines():
            if not line:
                if entries:
                    entries.append({})
                continue
            key, _, value = line.partition(" ")
            if key == "worktree":
                entries.append({"path": value})
            elif entries:
                entries[-1][key] = value
        found: dict[str, WorktreeInfo] = {}
        for entry in entries:
            if not entry.get("path") or "bare" in entry:
                continue
            worktree_path = Path(entry["path"]).resolve()
            found[self._key(worktree_path)] = WorktreeInfo(
                worktree_path, entry.get("branch", "").removeprefix("refs/heads/") or None,
                entry.get("HEAD", ""),
            )
        return found

    def _branch_exists(self, branch: str) -> bool:
        result = subprocess.run(["git", "-C", str(self.repository), "show-ref", "--verify",
                                 "--quiet", f"refs/heads/{branch}"], capture_output=True)
        if result.returncode not in (0, 1):
            raise WorktreeError("could not inspect local branch refs")
        return result.returncode == 0

    def _run(self, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(self.repository), *args],
                                capture_output=True, text=True, encoding="utf-8")
        if result.returncode:
            raise WorktreeError(result.stderr.strip() or f"git {' '.join(args)} failed")
        return result.stdout

    def _git_root(self, start: Path) -> Path:
        result = subprocess.run(["git", "-C", str(start.resolve()), "rev-parse", "--show-toplevel"],
                                capture_output=True, text=True, encoding="utf-8")
        if result.returncode:
            raise ValueError(f"not inside a Git repository: {start}")
        return Path(result.stdout.strip()).resolve()

    def _git_common_dir(self, start: Path) -> Path:
        result = subprocess.run(["git", "-C", str(start), "rev-parse", "--git-common-dir"],
                                capture_output=True, text=True, encoding="utf-8")
        if result.returncode:
            raise WorktreeError("could not resolve Git common directory")
        value = Path(result.stdout.strip())
        return (start / value).resolve() if not value.is_absolute() else value.resolve()

    def _require_managed(self, path: Path) -> None:
        if path == self.managed_root or self.managed_root not in path.parents:
            raise WorktreeError(f"path is outside the managed worktree root: {path}")

    @staticmethod
    def _key(path: Path) -> str:
        return str(path.resolve()).casefold() if path.drive else str(path.resolve())
