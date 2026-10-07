"""Exact-review-bound integration of task branches into the project branch."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from .gate import GateRunner
from .host_config import WindowsHostConfig
from .state import HeadLease, StateStore
from .tasks import TaskState


@dataclass(frozen=True, slots=True)
class IntegrationResult:
    task_id: str
    target_ref: str
    base_sha: str
    integrated_sha: str
    gate_fingerprint: str
    event_id: str


def integrate_approved_task(config: WindowsHostConfig, store: StateStore,
                            lease: HeadLease, task_id: str, gate_runner: GateRunner,
                            commands: dict[str, tuple[str, ...] | None], *,
                            allowed_environment: tuple[str, ...] = (),
                            expected_base_sha: str | None = None) -> IntegrationResult:
    """Merge a DONE task only after checking its exact approved commit in context.

    Integration is a host-owned fast-forward update. The current project
    checkout must be clean apart from its authoritative PLAN projection and
    host metadata. The integration candidate is built and gated in a temporary
    detached worktree before the target ref is advanced with compare-and-swap.
    """
    if os.name != "nt":
        raise RuntimeError("supervised branch integration is supported on Windows only")
    if not isinstance(config, WindowsHostConfig) or not isinstance(store, StateStore):
        raise ValueError("integration requires validated host configuration and StateStore")
    if not isinstance(lease, HeadLease) or store.path.resolve() != config.state_db.resolve():
        raise ValueError("integration requires the configured StateStore and current HEAD lease")
    if not isinstance(gate_runner, GateRunner) or not isinstance(commands, dict):
        raise ValueError("integration requires the configured mechanical gate")
    task = store.get_task(task_id)
    if task is None or task.state is not TaskState.DONE:
        raise ValueError("only a completed task can be integrated")

    reviews = [event for event in store.verified_review_events()
               if event["payload"].get("task_id") == task_id]
    if not reviews or reviews[-1]["payload"].get("decision") != "approved":
        raise ValueError("task has no latest verified independent approval receipt")
    approval = reviews[-1]["payload"]
    reviewed_sha = approval["sha"].lower()
    if approval.get("maker_identity") != task.maker_identity:
        raise ValueError("approved maker identity differs from the authoritative task snapshot")

    repository = config.project_root
    branch = _git(repository, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
    if not branch or branch.startswith("codexdevteam/"):
        raise ValueError("integration requires the checked-out project branch, not a task branch")
    transaction_id = uuid.uuid4().hex
    base_sha = _git(repository, "rev-parse", "HEAD").strip().lower()
    if expected_base_sha is not None and base_sha != expected_base_sha.lower():
        _escalate(store, lease, task_id, transaction_id,
                  "project branch moved since the task dispatch base")
        raise ValueError("project branch moved since the maker dispatch base")
    target_sha = _git(repository, "rev-parse", f"refs/heads/{branch}").strip().lower()
    if target_sha != base_sha:
        raise ValueError("checked-out project branch differs from its HEAD commit")
    status = _git(repository, "status", "--porcelain", "--untracked-files=all",
                  "--", ".", ":(exclude).codexdevteam/**", ":(exclude)PLAN.md")
    if status.strip():
        raise ValueError("project checkout must be clean outside host metadata and PLAN.md")
    plan_path = repository / "PLAN.md"
    if plan_path.is_symlink() or not plan_path.is_file():
        raise ValueError("integration requires a regular host-projected PLAN.md")
    plan_bytes = plan_path.read_bytes()

    task_ref = f"refs/heads/codexdevteam/{task_id}"
    task_sha = _git(repository, "rev-parse", task_ref).strip().lower()
    if task_sha != reviewed_sha:
        raise ValueError("task branch HEAD differs from the independently approved SHA")
    if not _shares_history(repository, base_sha, task_sha):
        _escalate(store, lease, task_id, transaction_id,
                  "approved task branch has no common history with the project branch")
        raise ValueError("approved task branch has no common history with the project branch")

    integration_branch = f"codexdevteam/integration/{task_id}/{transaction_id}"
    candidate_root = config.worktree_root / ("integration-" + transaction_id)
    if config.worktree_root.is_symlink():
        raise ValueError("configured integration worktree root cannot be a symlink")
    config.worktree_root.mkdir(parents=True, exist_ok=True)
    if candidate_root.exists() or candidate_root.is_symlink():
        raise ValueError("integration worktree path already exists")
    _git(repository, "worktree", "add", "-b", integration_branch,
         str(candidate_root), base_sha)
    try:
        merge = subprocess.run(
            ["git", "-C", str(candidate_root), "-c", "user.name=CODEXDEVTEAM",
             "-c", "user.email=codexdevteam@localhost", "merge", "--no-ff", "--no-edit",
             task_ref],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        if merge.returncode:
            subprocess.run(["git", "-C", str(candidate_root), "merge", "--abort"],
                           capture_output=True, check=False)
            _escalate(store, lease, task_id, transaction_id,
                      "reviewed task branch conflicts with the current project branch")
            raise ValueError("integration conflict; project branch was not changed")
        integrated_sha = _git(candidate_root, "rev-parse", "HEAD").strip().lower()
        gate = gate_runner.run(
            task, candidate_root, expected_sha=integrated_sha, base_ref=base_sha,
            commands=commands, allowed_environment=allowed_environment,
            open_task_ids=tuple(sorted(
                item.task_id for item in store.list_tasks()
                if item.state is not TaskState.DONE)),
            active_tasks=store.list_tasks(),
        )
        if gate.status != "passed":
            _escalate(store, lease, task_id, transaction_id,
                      "post-integration mechanical verification failed")
            raise ValueError("post-integration gate failed; project branch was not changed")
        try:
            gate_artifact_sha256 = hashlib.sha256(Path(gate.artifact_path).read_bytes()).hexdigest()
        except OSError as exc:
            raise ValueError("post-integration gate artifact is unavailable") from exc

        # Preserve the authoritative host-projected PLAN across the fast-forward
        # reset. The transaction record lets restart recovery restore it if the
        # process exits between ref update and worktree projection.
        if plan_path.is_symlink() or not plan_path.is_file() \
                or hashlib.sha256(plan_path.read_bytes()).digest() != hashlib.sha256(plan_bytes).digest():
            raise ValueError("PLAN.md changed while integration verification was running")
        transaction_root = config.control_root / "integration"
        _ensure_real_directory(config.project_root, transaction_root)
        journal_path = transaction_root / (transaction_id + ".json")
        backup_path = transaction_root / (transaction_id + ".PLAN")
        _write_exclusive(backup_path, plan_bytes)
        journal = {
            "protocol_version": 1,
            "transaction_id": transaction_id,
            "task_id": task_id,
            "target_ref": f"refs/heads/{branch}",
            "base_sha": base_sha,
            "integrated_sha": integrated_sha,
            "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
            "backup_name": backup_path.name,
            "gate_fingerprint": gate.fingerprint,
            "gate_artifact_sha256": gate_artifact_sha256,
            "gate_artifact_path": str(Path(gate.artifact_path).resolve()),
            "reviewed_sha": reviewed_sha,
        }
        _write_exclusive(journal_path, (json.dumps(journal, sort_keys=True) + "\n").encode())
        event_id = "branch-integration-" + transaction_id
        store.record_event(lease, event_id + ":prepared", {
            "type": "branch.integration_prepared", **journal,
        })
        current = _git(repository, "rev-parse", f"{branch}").strip().lower()
        current_head = _git(repository, "rev-parse", "HEAD").strip().lower()
        current_branch = _git(repository, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
        status = _git(repository, "status", "--porcelain", "--untracked-files=all",
                      "--", ".", ":(exclude).codexdevteam/**", ":(exclude)PLAN.md")
        if current != base_sha or current_head != base_sha or current_branch != branch or status.strip():
            _escalate(store, lease, task_id, transaction_id,
                      "project checkout changed after integration verification")
            raise ValueError("project checkout changed; integration was not applied")
        _git(repository, "update-ref", f"refs/heads/{branch}", integrated_sha, base_sha)
        _git(repository, "reset", "--hard", integrated_sha)
        _restore_plan(plan_path, backup_path, journal["plan_sha256"])
        store.record_event(lease, event_id + ":completed", {
            "type": "branch.integration_completed", "task_id": task_id,
            "target_ref": f"refs/heads/{branch}", "base_sha": base_sha,
            "integrated_sha": integrated_sha, "reviewed_sha": reviewed_sha,
            "gate_fingerprint": gate.fingerprint,
            "gate_artifact_sha256": gate_artifact_sha256,
            "gate_artifact_path": journal["gate_artifact_path"],
        })
        _resolve_integration_escalation(store, lease, task_id, reviewed_sha)
        journal_path.unlink(missing_ok=True)
        backup_path.unlink(missing_ok=True)
        return IntegrationResult(task_id, f"refs/heads/{branch}", base_sha,
                                 integrated_sha, gate.fingerprint, event_id)
    finally:
        # A conflicted or failed candidate remains available only while dirty;
        # clean temporary worktrees can be removed without touching task work.
        if candidate_root.exists():
            candidate_status = subprocess.run(
                ["git", "-C", str(candidate_root), "status", "--porcelain",
                 "--untracked-files=all"], capture_output=True, text=True, check=False,
            )
            if candidate_status.returncode == 0 and not candidate_status.stdout.strip():
                subprocess.run(["git", "-C", str(repository), "worktree", "remove",
                                str(candidate_root)], capture_output=True, check=False)
                subprocess.run(["git", "-C", str(repository), "branch", "-D",
                                integration_branch], capture_output=True, check=False)


def recover_pending_integrations(config: WindowsHostConfig, store: StateStore,
                                 lease: HeadLease) -> tuple[str, ...]:
    """Finish interrupted ref projection and return tasks needing attention."""
    repository = config.project_root
    root = config.control_root / "integration"
    if not root.exists() and not root.is_symlink():
        return ()
    if root.is_symlink() or not root.is_dir():
        raise ValueError("integration transaction directory is unsafe")
    attention: list[str] = []
    for journal_path in sorted(root.glob("*.json")):
        if journal_path.is_symlink() or not journal_path.is_file() or journal_path.stat().st_size > 16_384:
            raise ValueError("integration journal is unsafe or too large")
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("integration journal cannot be read") from exc
        required = {"protocol_version", "transaction_id", "task_id", "target_ref",
                    "base_sha", "integrated_sha", "plan_sha256", "backup_name",
                    "gate_fingerprint", "gate_artifact_sha256", "gate_artifact_path",
                    "reviewed_sha"}
        if (not isinstance(journal, dict) or set(journal) != required
                or journal.get("protocol_version") != 1
                or not isinstance(journal.get("transaction_id"), str)
                or journal_path.stem != journal["transaction_id"]
                or not isinstance(journal.get("task_id"), str)
                or not isinstance(journal.get("target_ref"), str)
                or not re.fullmatch(r"refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]*",
                                    journal["target_ref"])
                or ".." in journal["target_ref"]
                or not all(isinstance(journal.get(key), str)
                           for key in ("base_sha", "integrated_sha", "plan_sha256",
                                       "backup_name", "gate_fingerprint",
                                       "gate_artifact_sha256", "gate_artifact_path",
                                       "reviewed_sha"))):
            raise ValueError("integration journal schema is invalid")
        artifact = Path(journal["gate_artifact_path"])
        artifacts_root = (repository / ".codexdevteam" / "gates").resolve(strict=True)
        if (artifact.is_symlink() or not artifact.is_file()
                or (artifact.resolve(strict=True) != artifacts_root
                    and artifacts_root not in artifact.resolve(strict=True).parents)
                or hashlib.sha256(artifact.read_bytes()).hexdigest()
                != journal["gate_artifact_sha256"]):
            raise ValueError("integration recovery gate artifact is missing or changed")
        target_ref = journal["target_ref"]
        if not target_ref.startswith("refs/heads/") or ".." in target_ref:
            raise ValueError("integration journal target ref is invalid")
        backup_name = journal["backup_name"]
        if Path(backup_name).name != backup_name or not backup_name.endswith(".PLAN"):
            raise ValueError("integration journal backup name is invalid")
        backup = root / backup_name
        plan_path = repository / "PLAN.md"
        transaction_id = journal["transaction_id"]
        task_id = journal["task_id"]
        current = _git(repository, "rev-parse", target_ref).strip().lower()
        if current == journal["integrated_sha"].lower():
            # Determine whether the interrupted host stopped before or after
            # projecting the new tree. Never reset over unrelated local edits.
            checked_out = _git(repository, "symbolic-ref", "--quiet", "HEAD").strip()
            if checked_out != target_ref:
                raise ValueError("integration recovery target is no longer checked out")
            is_base = _matches_tree(repository, journal["base_sha"])
            is_integrated = _matches_tree(repository, journal["integrated_sha"])
            if is_base:
                _git(repository, "reset", "--hard", journal["integrated_sha"])
            elif not is_integrated:
                raise ValueError("integration recovery found unrelated project worktree changes")
            temporary = plan_path.with_name(".PLAN.codexdevteam-restore")
            if temporary.exists() and not temporary.is_symlink():
                if hashlib.sha256(temporary.read_bytes()).hexdigest() == journal["plan_sha256"]:
                    temporary.unlink()
                else:
                    raise ValueError("integration recovery found an unverified PLAN temporary")
            _restore_plan(plan_path, backup, journal["plan_sha256"])
            completed_id = "branch-integration-" + transaction_id
            store.record_event(lease, completed_id + ":completed", {
                "type": "branch.integration_completed", "task_id": task_id,
                "target_ref": target_ref, "base_sha": journal["base_sha"],
                "integrated_sha": journal["integrated_sha"],
                "reviewed_sha": journal["reviewed_sha"],
                "gate_fingerprint": journal["gate_fingerprint"],
                "gate_artifact_sha256": journal["gate_artifact_sha256"],
                "gate_artifact_path": journal["gate_artifact_path"],
            })
            _resolve_integration_escalation(store, lease, task_id, journal["reviewed_sha"])
        elif current == journal["base_sha"].lower():
            store.record_event(lease, "branch-integration-" + transaction_id + ":aborted", {
                "type": "branch.integration_aborted", "task_id": task_id,
                "target_ref": target_ref, "base_sha": journal["base_sha"],
                "candidate_sha": journal["integrated_sha"],
                "reason": "host stopped before advancing the target branch",
            })
            _escalate(store, lease, task_id, transaction_id,
                      "approved task was not integrated because the host stopped before advancing the project branch")
            attention.append(task_id)
            backup.unlink(missing_ok=True)
        else:
            _escalate(store, lease, task_id, transaction_id,
                      "target branch changed during integration recovery")
            raise ValueError("integration recovery target ref differs from both recorded commits")
        journal_path.unlink(missing_ok=True)
    return tuple(sorted(set(attention)))


def find_approved_tasks_awaiting_integration(store: StateStore,
                                            lease: HeadLease) -> tuple[str, ...]:
    """Fence dispatch when a DONE approved branch lacks a completion receipt."""
    reviews_by_task: dict[str, dict] = {}
    for event in store.verified_review_events():
        payload = event["payload"]
        reviews_by_task[payload["task_id"]] = payload
    completed = {
        (event["payload"].get("task_id"), event["payload"].get("reviewed_sha"))
        for event in store.events()
        if event["payload"].get("type") == "branch.integration_completed"
    }
    pending: list[str] = []
    for task in store.list_tasks():
        review = reviews_by_task.get(task.task_id)
        if (task.state is not TaskState.DONE or review is None
                or review.get("decision") != "approved"
                or (task.task_id, review.get("sha")) in completed):
            continue
        event_digest = hashlib.sha256(
            (task.task_id + "\0" + str(review.get("sha"))).encode("utf-8")
        ).hexdigest()[:20]
        store.record_escalation(
            lease, escalation_key=f"{task.task_id}:integration", task_id=task.task_id,
            severity="high",
            message=("Task has an approved completed branch but no verified project "
                     "integration receipt; inspect its integration gate and branch before dispatch."),
            event_id=f"approved-integration-missing:{lease.generation}:{event_digest}",
        )
        pending.append(task.task_id)
    return tuple(sorted(pending))


def _matches_tree(repository: Path, commit: str) -> bool:
    diff = subprocess.run(
        ["git", "-C", str(repository), "diff", "--quiet", commit, "--", ".",
         ":(exclude).codexdevteam/**", ":(exclude)PLAN.md"],
        capture_output=True, check=False,
    )
    if diff.returncode not in {0, 1}:
        raise ValueError("integration recovery could not compare project files")
    untracked = _git(repository, "ls-files", "--others", "--exclude-standard", "--", ".",
                     ":(exclude).codexdevteam/**", ":(exclude)PLAN.md")
    return diff.returncode == 0 and not untracked.strip()


def _restore_plan(plan_path: Path, backup_path: Path, expected_sha256: str) -> None:
    if backup_path.is_symlink() or not backup_path.is_file():
        raise ValueError("integration PLAN recovery backup is missing or unsafe")
    contents = backup_path.read_bytes()
    if hashlib.sha256(contents).hexdigest() != expected_sha256:
        raise ValueError("integration PLAN recovery backup failed its hash check")
    temporary = plan_path.with_name(".PLAN.codexdevteam-restore")
    if temporary.exists() or temporary.is_symlink():
        raise ValueError("integration PLAN restore temporary path already exists")
    temporary.write_bytes(contents)
    os.replace(temporary, plan_path)


def _ensure_real_directory(root: Path, directory: Path) -> None:
    resolved_root = root.resolve(strict=True)
    try:
        relative = directory.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("integration transaction directory escaped project control root") from exc
    current = resolved_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("integration transaction directory cannot traverse symlinks")
    directory.mkdir(parents=True, exist_ok=True)
    resolved_directory = directory.resolve(strict=True)
    if resolved_root != resolved_directory and resolved_root not in resolved_directory.parents:
        raise ValueError("integration transaction directory escaped project control root")


def _write_exclusive(path: Path, contents: bytes) -> None:
    if path.is_symlink():
        raise ValueError("integration transaction file cannot be a symlink")
    with path.open("xb") as stream:
        stream.write(contents)
        stream.flush()
        os.fsync(stream.fileno())


def _shares_history(repository: Path, left: str, right: str) -> bool:
    result = subprocess.run(["git", "-C", str(repository), "merge-base", left, right],
                            capture_output=True, check=False)
    if result.returncode not in {0, 1}:
        raise ValueError("could not verify task branch ancestry")
    return result.returncode == 0


def _resolve_integration_escalation(store: StateStore, lease: HeadLease,
                                    task_id: str, reviewed_sha: str) -> None:
    key = f"{task_id}:integration"
    is_open = False
    for event in store.events():
        payload = event["payload"]
        if payload.get("escalation_key") != key:
            continue
        if payload.get("type") == "escalation.recorded":
            is_open = True
        elif payload.get("type") == "escalation.resolved":
            is_open = False
    if is_open:
        digest = hashlib.sha256(reviewed_sha.encode("ascii")).hexdigest()[:16]
        store.resolve_escalation(
            lease, key, event_id=f"branch-integration-resolved:{task_id}:{digest}",
        )


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repository), *args], capture_output=True,
                            text=True, encoding="utf-8", check=False)
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def _escalate(store: StateStore, lease: HeadLease, task_id: str,
              transaction_id: str, message: str) -> None:
    store.record_escalation(
        lease, escalation_key=f"{task_id}:integration", task_id=task_id,
        severity="high", message=message,
        event_id=f"branch-integration-escalation:{transaction_id}",
    )
