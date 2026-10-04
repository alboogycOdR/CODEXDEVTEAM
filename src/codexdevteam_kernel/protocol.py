"""Versioned, provider-neutral task record used by coordination adapters."""

import re
from dataclasses import dataclass
from fnmatch import fnmatchcase

from .tasks import TaskState
from .territory import normalize_repo_path


PROTOCOL_VERSION = 1
_TASK_ID = re.compile(r"^TASK-[A-Z0-9][A-Z0-9-]*$")
_FIELDS = {
    "protocol_version", "task_id", "title", "state", "assigned_worker",
    "priority", "owned_paths", "protected_grants", "depends_on",
    "acceptance_criteria", "test_evidence",
    "maker_identity",
    "owns_failures",
    "task_class",
}


@dataclass(frozen=True, slots=True)
class TaskRecord:
    task_id: str
    title: str
    state: TaskState
    assigned_worker: str | None
    priority: str
    owned_paths: tuple[str, ...]
    protected_grants: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    test_evidence: tuple[str, ...] = ()
    protocol_version: int = PROTOCOL_VERSION
    maker_identity: dict[str, str] | None = None
    owns_failures: tuple[str, ...] = ()
    task_class: str | None = None

    def __post_init__(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError(f"unsupported task protocol version: {self.protocol_version}")
        if not _TASK_ID.fullmatch(self.task_id):
            raise ValueError("task_id must match TASK-[A-Z0-9][A-Z0-9-]*")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("title must be non-empty")
        if self.task_class is not None and (
            not isinstance(self.task_class, str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.task_class)
        ):
            raise ValueError("task_class must be null or a lowercase policy key")
        if not isinstance(self.state, TaskState):
            raise ValueError("state must be a TaskState")
        if self.assigned_worker is not None and (
            not isinstance(self.assigned_worker, str) or not self.assigned_worker.strip()
        ):
            raise ValueError("assigned_worker must be null or a non-empty string")
        if self.maker_identity is not None:
            required_identity = {"unit_id", "runtime", "model"}
            if not isinstance(self.maker_identity, dict) or set(self.maker_identity) != required_identity or not all(
                isinstance(value, str) and value.strip() for value in self.maker_identity.values()
            ):
                raise ValueError("maker_identity must snapshot unit_id, runtime, and model")
        if not isinstance(self.owns_failures, tuple) or not all(
            isinstance(task_id, str) and _TASK_ID.fullmatch(task_id)
            for task_id in self.owns_failures
        ):
            raise ValueError("owns_failures must contain open task IDs")
        if len(set(self.owns_failures)) != len(self.owns_failures):
            raise ValueError("owns_failures must not contain duplicates")
        if self.priority not in {"critical", "high", "medium", "low"}:
            raise ValueError("priority must be critical, high, medium, or low")
        if not self.owned_paths:
            raise ValueError("owned_paths must contain at least one path pattern")
        for path in (*self.owned_paths, *self.protected_grants):
            normalize_repo_path(path)
        for grant in self.protected_grants:
            if not grant_within_owned(grant, self.owned_paths):
                raise ValueError(f"protected grant is outside owned_paths: {grant}")
        for dependency in self.depends_on:
            if not _TASK_ID.fullmatch(dependency):
                raise ValueError(f"invalid dependency task id: {dependency}")
        if self.task_id in self.depends_on:
            raise ValueError("a task cannot depend on itself")
        for name, values in (("owned_paths", self.owned_paths),
                             ("protected_grants", self.protected_grants),
                             ("depends_on", self.depends_on)):
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must not contain duplicates")
        for name, values in (("owned_paths", self.owned_paths),
                             ("protected_grants", self.protected_grants),
                             ("depends_on", self.depends_on),
                             ("acceptance_criteria", self.acceptance_criteria),
                             ("test_evidence", self.test_evidence)):
            if not all(isinstance(value, str) and value.strip() for value in values):
                raise ValueError(f"{name} values must be non-empty strings")

    def to_dict(self) -> dict:
        return {
            "protocol_version": self.protocol_version,
            "task_id": self.task_id,
            "title": self.title,
            "state": self.state.value,
            "assigned_worker": self.assigned_worker,
            "priority": self.priority,
            "owned_paths": list(self.owned_paths),
            "protected_grants": list(self.protected_grants),
            "depends_on": list(self.depends_on),
            "acceptance_criteria": list(self.acceptance_criteria),
            "test_evidence": list(self.test_evidence),
            "maker_identity": dict(self.maker_identity) if self.maker_identity else None,
            "owns_failures": list(self.owns_failures),
            "task_class": self.task_class,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskRecord":
        if not isinstance(data, dict):
            raise ValueError("task record must be an object")
        unknown = set(data) - _FIELDS
        missing = _FIELDS - {"protected_grants", "depends_on", "acceptance_criteria", "test_evidence", "maker_identity", "owns_failures", "task_class"} - set(data)
        if unknown:
            raise ValueError(f"unknown task fields: {', '.join(sorted(unknown))}")
        if missing:
            raise ValueError(f"missing task fields: {', '.join(sorted(missing))}")
        try:
            return cls(
                task_id=data["task_id"],
                title=data["title"],
                state=TaskState(data["state"]),
                assigned_worker=data["assigned_worker"],
                priority=data["priority"],
                owned_paths=tuple(data["owned_paths"]),
                protected_grants=tuple(data.get("protected_grants", ())),
                depends_on=tuple(data.get("depends_on", ())),
                acceptance_criteria=tuple(data.get("acceptance_criteria", ())),
                test_evidence=tuple(data.get("test_evidence", ())),
                maker_identity=data.get("maker_identity"),
                owns_failures=tuple(data.get("owns_failures", ())),
                task_class=data.get("task_class"),
                protocol_version=data["protocol_version"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid task record: {exc}") from exc


def validate_task_set(tasks: tuple[TaskRecord, ...] | list[TaskRecord], *,
                      archived_task_ids: tuple[str, ...] | list[str] = ()) -> list[str]:
    """Return structural task-set findings without accessing a repository."""
    findings: list[str] = []
    if not isinstance(archived_task_ids, (tuple, list)):
        return ["archived_task_ids must be a tuple or list"]
    archived: set[str] = set()
    for task_id in archived_task_ids:
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            findings.append(f"invalid archived task id: {task_id!r}")
            continue
        if task_id in archived:
            findings.append(f"duplicate archived task id: {task_id}")
        archived.add(task_id)
    by_id: dict[str, TaskRecord] = {}
    for task in tasks:
        if task.task_id in by_id:
            findings.append(f"duplicate task id: {task.task_id}")
        by_id[task.task_id] = task
    for task in tasks:
        if task.task_id in archived:
            findings.append(f"task id appears in both active set and archive: {task.task_id}")
        for dependency in task.depends_on:
            if dependency not in by_id and dependency not in archived:
                findings.append(f"{task.task_id} depends on missing task {dependency}")
        for owner_id in task.owns_failures:
            owner = by_id.get(owner_id)
            if owner_id in archived:
                findings.append(f"{task.task_id} owns failures assigned to completed task {owner_id}")
            elif owner is None:
                findings.append(f"{task.task_id} owns failures assigned to missing task {owner_id}")
            elif owner.task_id == task.task_id:
                findings.append(f"{task.task_id} cannot own its own inherited failures")
            elif owner.state is TaskState.DONE:
                findings.append(f"{task.task_id} owns failures assigned to completed task {owner_id}")
    active = [task for task in tasks if task.state in {
        TaskState.CLAIMED, TaskState.IN_PROGRESS, TaskState.NEEDS_REVIEW
    }]
    for index, left in enumerate(active):
        for right in active[index + 1:]:
            if _territories_intersect(left.owned_paths, right.owned_paths):
                findings.append(f"active territory overlaps: {left.task_id} and {right.task_id}")
    return findings


def _territories_intersect(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    """Conservatively decide whether two segment globs can match one path."""
    def segment_overlap(a: str, b: str) -> bool:
        if a == b:
            return True
        a_glob = any(char in a for char in "*?[")
        b_glob = any(char in b for char in "*?[")
        if not a_glob:
            return fnmatchcase(a, b)
        if not b_glob:
            return fnmatchcase(b, a)
        return True  # General glob intersection is undecidable cheaply; fail safe.

    def patterns_overlap(a: str, b: str) -> bool:
        aa, bb = a.split("/"), b.split("/")
        seen: set[tuple[int, int]] = set()

        def walk(i: int, j: int) -> bool:
            if (i, j) in seen:
                return False
            seen.add((i, j))
            if i == len(aa):
                return all(part == "**" for part in bb[j:])
            if j == len(bb):
                return all(part == "**" for part in aa[i:])
            if aa[i] == "**" and bb[j] == "**":
                return walk(i + 1, j) or walk(i, j + 1)
            if aa[i] == "**":
                return walk(i + 1, j) or walk(i, j + 1)
            if bb[j] == "**":
                return walk(i, j + 1) or walk(i + 1, j)
            return segment_overlap(aa[i], bb[j]) and walk(i + 1, j + 1)

        return walk(0, 0)

    return any(patterns_overlap(a, b) for a in left for b in right)


def grant_within_owned(grant: str, owned: tuple[str, ...]) -> bool:
    """Conservatively require grant prefix to sit within an owned path prefix."""
    if grant in owned:
        return True
    grant_prefix = _glob_prefix(grant)
    if not grant_prefix:
        return False
    for pattern in owned:
        owned_prefix = _glob_prefix(pattern)
        if not owned_prefix or grant_prefix == owned_prefix or grant_prefix.startswith(owned_prefix + "/"):
            return True
    return False


def _glob_prefix(pattern: str) -> str:
    for index, character in enumerate(pattern):
        if character in "*?[":
            return pattern[:index].rstrip("/")
    return pattern.rstrip("/")
