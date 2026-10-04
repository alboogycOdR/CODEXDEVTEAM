"""Provider-neutral task state vocabulary and transition rules."""

from enum import StrEnum


class TaskState(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"
    DONE = "done"


_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset({TaskState.CLAIMED}),
    TaskState.CLAIMED: frozenset({TaskState.IN_PROGRESS, TaskState.BLOCKED}),
    TaskState.IN_PROGRESS: frozenset({TaskState.CLAIMED, TaskState.NEEDS_REVIEW, TaskState.BLOCKED}),
    TaskState.NEEDS_REVIEW: frozenset({TaskState.IN_PROGRESS, TaskState.DONE, TaskState.BLOCKED}),
    TaskState.BLOCKED: frozenset({TaskState.PENDING, TaskState.IN_PROGRESS}),
    TaskState.DONE: frozenset(),
}


def allowed_transition(source: TaskState, target: TaskState, *,
                       assigned_worker: bool = False,
                       head_authority: bool = False) -> bool:
    """Return whether an actor with these authority facts may change state.

    Assigned workers may claim/start/block/submit their task. Only the active
    HEAD may reassign/unblock/review/complete it. The caller must establish
    `head_authority` from a valid HEAD lease; this module does not infer it from
    a provider or unit name.
    """
    if target not in _TRANSITIONS[source]:
        return False
    if source is TaskState.PENDING and target is TaskState.CLAIMED:
        return assigned_worker or head_authority
    if source is TaskState.IN_PROGRESS and target is TaskState.CLAIMED:
        return head_authority
    if source in {TaskState.CLAIMED, TaskState.IN_PROGRESS} and target in {
        TaskState.IN_PROGRESS, TaskState.NEEDS_REVIEW, TaskState.BLOCKED
    }:
        return assigned_worker or head_authority
    return head_authority
