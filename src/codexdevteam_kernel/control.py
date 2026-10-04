"""Typed builder-to-HEAD CONTROL messages and authorization checks."""

from dataclasses import dataclass
import re

from .protocol import TaskRecord
from .tasks import TaskState


CONTROL_VERSION = 1
_MESSAGE_FIELDS = {
    "protocol_version", "event_id", "task_id", "worker_id", "requested_state",
    "progress_note", "blocked_reason", "artifacts", "test_evidence", "head_sha",
    "invocation_id",
}


@dataclass(frozen=True, slots=True)
class ControlMessage:
    event_id: str
    task_id: str
    worker_id: str
    requested_state: TaskState | None = None
    progress_note: str | None = None
    blocked_reason: str | None = None
    artifacts: tuple[str, ...] = ()
    test_evidence: tuple[str, ...] = ()
    protocol_version: int = CONTROL_VERSION
    head_sha: str | None = None
    invocation_id: str | None = None

    def __post_init__(self) -> None:
        if self.protocol_version != CONTROL_VERSION:
            raise ValueError(f"unsupported CONTROL protocol version: {self.protocol_version}")
        for name in ("event_id", "task_id", "worker_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.requested_state is not None and not isinstance(self.requested_state, TaskState):
            raise ValueError("requested_state must be a TaskState or null")
        if self.head_sha is not None and not re.fullmatch(r"[0-9a-fA-F]{40,64}", self.head_sha):
            raise ValueError("head_sha must be null or a full Git SHA")
        if self.invocation_id is not None and not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", self.invocation_id):
            raise ValueError("invocation_id must be null or a valid invocation ID")
        if self.progress_note is not None and (
            not isinstance(self.progress_note, str) or not self.progress_note.strip()
        ):
            raise ValueError("progress_note must be null or a non-empty string")
        for name, values in (("artifacts", self.artifacts), ("test_evidence", self.test_evidence)):
            if not isinstance(values, tuple) or not all(
                isinstance(value, str) and value.strip() for value in values
            ):
                raise ValueError(f"{name} must be a tuple of non-empty strings")
        if self.blocked_reason is not None and (
            not isinstance(self.blocked_reason, str) or not self.blocked_reason.strip()
        ):
            raise ValueError("blocked_reason must be null or a non-empty string")

    @classmethod
    def from_dict(cls, data: dict) -> "ControlMessage":
        if not isinstance(data, dict):
            raise ValueError("CONTROL message must be an object")
        unknown = set(data) - _MESSAGE_FIELDS
        required = {"event_id", "task_id", "worker_id"}
        missing = required - set(data)
        if unknown:
            raise ValueError(f"unknown CONTROL fields: {', '.join(sorted(unknown))}")
        if missing:
            raise ValueError(f"missing CONTROL fields: {', '.join(sorted(missing))}")
        for name in ("artifacts", "test_evidence"):
            if name in data and not isinstance(data[name], list):
                raise ValueError(f"{name} must be a list")
        try:
            requested = data.get("requested_state")
            return cls(
                protocol_version=data.get("protocol_version", CONTROL_VERSION),
                event_id=data["event_id"], task_id=data["task_id"], worker_id=data["worker_id"],
                requested_state=TaskState(requested) if requested is not None else None,
                head_sha=data.get("head_sha"),
                invocation_id=data.get("invocation_id"),
                progress_note=data.get("progress_note"),
                blocked_reason=data.get("blocked_reason"),
                artifacts=tuple(data.get("artifacts", ())),
                test_evidence=tuple(data.get("test_evidence", ())),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid CONTROL message: {exc}") from exc


@dataclass(frozen=True, slots=True)
class ControlDecision:
    accepted: bool
    findings: tuple[str, ...]


def validate_control(message: ControlMessage, task: TaskRecord) -> ControlDecision:
    """Validate a builder report; only HEAD can apply it to authoritative state."""
    findings: list[str] = []
    if message.task_id != task.task_id:
        findings.append("message task_id does not match the task record")
    if task.assigned_worker != message.worker_id:
        findings.append("worker is not assigned to this task")
    if task.state not in {TaskState.CLAIMED, TaskState.IN_PROGRESS}:
        findings.append(f"worker cannot report against task in {task.state.value} state")
    target = message.requested_state
    if target is TaskState.DONE:
        findings.append("builders cannot mark tasks done")
    elif target is TaskState.NEEDS_REVIEW:
        if task.state is not TaskState.IN_PROGRESS:
            findings.append("needs_review can only be requested from in_progress")
        if not message.test_evidence:
            findings.append("needs_review requires test evidence")
        if not message.head_sha:
            findings.append("needs_review requires the tested head_sha")
    elif target is TaskState.BLOCKED:
        if task.state not in {TaskState.CLAIMED, TaskState.IN_PROGRESS}:
            findings.append("blocked can only be requested from claimed or in_progress")
        if not message.blocked_reason:
            findings.append("blocked requires a reason")
    elif target is TaskState.IN_PROGRESS and task.state is not TaskState.CLAIMED:
        findings.append("in_progress can only be requested from claimed")
    elif target not in {None, TaskState.IN_PROGRESS, TaskState.NEEDS_REVIEW, TaskState.BLOCKED}:
        findings.append(f"builders cannot request state {target.value}")
    return ControlDecision(not findings, tuple(findings))
