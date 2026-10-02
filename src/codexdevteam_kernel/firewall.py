"""Provider-neutral write authorization policy for runtime hook adapters."""

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .identity import WorkerIdentity
from .protocol import TaskRecord
from .tasks import TaskState
from .territory import TerritoryDecision, decide_write, normalize_repo_path


_MUTABLE_STATES = {TaskState.CLAIMED, TaskState.IN_PROGRESS}
_RESERVED_STATES = _MUTABLE_STATES | {TaskState.NEEDS_REVIEW}


@dataclass(frozen=True, slots=True)
class WriteAuthorization:
    allowed: bool
    decisions: tuple[TerritoryDecision, ...]


class TerritoryPolicy:
    """Authorize proposed repository-relative writes against durable task facts.

    Runtime adapters translate native events to this API. This API authorizes
    builder writes only; HEAD mutations must use the separate lease-checked
    state path. A role or provider identity alone never confers write authority.
    """

    def __init__(self, repository: str | Path, *,
                 protected_paths: Iterable[str] = ("PLAN.md",)):
        self.repository = Path(repository).resolve(strict=True)
        if not self.repository.is_dir():
            raise ValueError("repository must be an existing directory")
        self.protected_paths = tuple(normalize_repo_path(path) for path in protected_paths)

    def authorize(self, *, actor: WorkerIdentity | None, task_id: str | None,
                  paths: Iterable[str], tasks: Iterable[TaskRecord]) -> WriteAuthorization:
        """Return one decision per path; any invalid path or actor fails closed."""
        proposed = tuple(paths)
        task_list = tuple(tasks)
        if not all(isinstance(path, str) for path in proposed):
            return WriteAuthorization(False, (TerritoryDecision(
                False, "", "write request contains an invalid path value"),))
        if not all(isinstance(item, TaskRecord) for item in task_list):
            return self._deny_all(proposed, "task set contains an invalid record")
        if not proposed:
            return WriteAuthorization(False, (TerritoryDecision(
                False, "", "write request contains no paths"),))
        if actor is None or not isinstance(actor, WorkerIdentity):
            return self._deny_all(proposed, "unknown actor")
        if actor.role.casefold() == "head":
            return self._deny_all(proposed, "HEAD writes require the lease-checked state path")
        if not isinstance(task_id, str) or not task_id:
            return self._deny_all(proposed, "write request has no task binding")
        matching_tasks = [item for item in task_list if item.task_id == task_id]
        if not matching_tasks:
            return self._deny_all(proposed, "unknown task")
        if len(matching_tasks) != 1:
            return self._deny_all(proposed, "task set has an ambiguous task ID")
        task = matching_tasks[0]
        if task.assigned_worker != actor.unit_id:
            return self._deny_all(proposed, "actor is not the task's assigned worker")
        if task.state not in _MUTABLE_STATES:
            return self._deny_all(proposed, "task state does not permit builder writes")

        for other in task_list:
            if (other.task_id != task.task_id and other.state in _RESERVED_STATES
                    and other.assigned_worker):
                for candidate in proposed:
                    try:
                        normalized = normalize_repo_path(candidate)
                    except ValueError:
                        continue
                    conflict = decide_write(normalized, other.owned_paths)
                    if conflict.allowed:
                        return self._deny_all(
                            proposed, f"path is reserved by active task {other.task_id}")
        decisions = self._check_paths(proposed, task.owned_paths,
                                      task.protected_grants)
        return WriteAuthorization(all(item.allowed for item in decisions), decisions)

    def _check_paths(self, paths: tuple[str, ...], owned: tuple[str, ...],
                     grants: tuple[str, ...]) -> tuple[TerritoryDecision, ...]:
        decisions: list[TerritoryDecision] = []
        for raw in paths:
            try:
                path = normalize_repo_path(raw)
                resolved = (self.repository / Path(*path.split("/"))).resolve(strict=False)
                if not resolved.is_relative_to(self.repository):
                    decisions.append(TerritoryDecision(False, path,
                                                       "path resolves outside repository"))
                    continue
                if path == "PLAN.md":
                    decisions.append(TerritoryDecision(False, path,
                                                       "PLAN.md is controlled through HEAD"))
                    continue
                protected = decide_write(path, (), protected_paths=self.protected_paths)
                granted = decide_write(path, grants)
                if protected.reason == "protected path" and not granted.allowed:
                    decisions.append(TerritoryDecision(False, path, "protected path"))
                    continue
                decision = decide_write(path, owned)
                if decision.allowed:
                    decisions.append(decision)
                elif granted.allowed:
                    decisions.append(TerritoryDecision(True, path,
                                                       "within supervisor-authorized protected grant"))
                else:
                    decisions.append(decision)
            except (ValueError, OSError, RuntimeError) as exc:
                decisions.append(TerritoryDecision(False, str(raw), f"invalid or unsafe path: {exc}"))
        return tuple(decisions)

    @staticmethod
    def _deny_all(paths: tuple[str, ...], reason: str) -> WriteAuthorization:
        return WriteAuthorization(False, tuple(TerritoryDecision(False, str(path), reason)
                                                for path in paths))
