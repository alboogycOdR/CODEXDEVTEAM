"""Deterministic assignment planning; runtime launch remains an adapter concern."""

from dataclasses import field, replace
from dataclasses import dataclass
import json
import math
import re
import time
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .identity import WorkerIdentity
from .protocol import TaskRecord
from .registry import WorkerDefinition, WorkerRegistry
from .tasks import TaskState


class DispatchError(ValueError):
    """Raised when assignment policy cannot produce a valid worker assignment."""


@dataclass(frozen=True, slots=True)
class WorkerReadiness:
    """Observable lifecycle dimensions; availability does not imply eligibility."""

    worker_id: str
    defined: bool
    active: bool
    availability: str
    eligible: bool
    assigned_tasks: tuple[str, ...]
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.availability not in {"available", "unavailable", "unknown"}:
            raise ValueError("invalid worker availability")


@dataclass(frozen=True, slots=True)
class TaskClassPolicy:
    """Map task classes to configured capability floors and logical worker roles."""

    capability_floors: Mapping[str, str]
    roles: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.capability_floors, Mapping):
            raise ValueError("task-class capability_floors must be a mapping")
        rules = dict(self.capability_floors)
        if any(not isinstance(task_class, str)
               or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", task_class)
               or not isinstance(floor, str) or not floor.strip()
               for task_class, floor in rules.items()):
            raise ValueError("task-class rules require lowercase class keys and capability labels")
        object.__setattr__(self, "capability_floors", MappingProxyType(rules))
        role_rules = dict(self.roles)
        if (not set(role_rules).issubset(rules)
                or any(not isinstance(task_class, str)
                       or not isinstance(role, str)
                       or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", role)
                       for task_class, role in role_rules.items())):
            raise ValueError("task-class role rules must map configured classes to valid roles")
        object.__setattr__(self, "roles", MappingProxyType(role_rules))

    def floor_for(self, task_class: str | None, *, fallback: str | None = None) -> str | None:
        if task_class is None:
            return fallback
        try:
            return self.capability_floors[task_class]
        except KeyError as exc:
            raise DispatchError(f"task class has no configured routing rule: {task_class}") from exc

    def role_for(self, task_class: str | None, *, fallback: str) -> str:
        if task_class is None:
            return fallback
        self.floor_for(task_class)
        return self.roles.get(task_class, fallback)

    @classmethod
    def from_dict(cls, data: dict) -> "TaskClassPolicy":
        if (not isinstance(data, dict)
                or set(data) - {"capability_floors", "roles"}
                or "capability_floors" not in data):
            raise ValueError("task-class policy requires capability_floors and optional roles")
        floors = data["capability_floors"]
        if not isinstance(floors, dict):
            raise ValueError("capability_floors must be an object")
        roles = data.get("roles", {})
        if not isinstance(roles, dict):
            raise ValueError("task-class roles must be an object")
        return cls(floors, roles)

    @classmethod
    def from_file(cls, path: str | Path) -> "TaskClassPolicy":
        """Load project-owned JSON policy without embedding model/provider IDs."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("task-class policy file is missing or invalid JSON") from exc
        return cls.from_dict(data)

    def to_dict(self) -> dict:
        result = {"capability_floors": dict(self.capability_floors)}
        if self.roles:
            result["roles"] = dict(self.roles)
        return result


@dataclass(frozen=True, slots=True)
class CapacityObservation:
    """Time-bounded provider-neutral worker availability and quota snapshot."""

    available: bool
    free_slots: int
    observed_at: float
    stale_after_seconds: float = 300.0
    quota_remaining: float | None = None
    cooldown_until: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.available, bool):
            raise ValueError("available must be boolean")
        if not isinstance(self.free_slots, int) or isinstance(self.free_slots, bool) or self.free_slots < 0:
            raise ValueError("free_slots must be a non-negative integer")
        numeric_values = (self.observed_at, self.stale_after_seconds,
                          *(() if self.quota_remaining is None else (self.quota_remaining,)),
                          *(() if self.cooldown_until is None else (self.cooldown_until,)))
        if any(not isinstance(value, (int, float)) or isinstance(value, bool)
               or not math.isfinite(value) for value in numeric_values):
            raise ValueError("capacity timestamps, quota, and freshness must be finite numbers")
        if self.quota_remaining is not None and self.quota_remaining < 0:
            raise ValueError("quota_remaining must be non-negative or null")
        if self.stale_after_seconds < 0:
            raise ValueError("stale_after_seconds must be non-negative")

    def eligible(self, *, now: float) -> bool:
        if now < self.observed_at or now - self.observed_at >= self.stale_after_seconds:
            return False
        if self.cooldown_until is not None and now < self.cooldown_until:
            return False
        return (self.available and self.free_slots > 0
                and (self.quota_remaining is None or self.quota_remaining > 0))


def assign_task(task: TaskRecord, worker: WorkerDefinition, *, machine_id: str | None = None,
                require_strict: bool = False) -> TaskRecord:
    """Snapshot the selected maker identity on a pending task before it is claimed."""
    if task.state is not TaskState.PENDING:
        raise DispatchError("only pending tasks can be assigned")
    if task.assigned_worker is not None and worker.identity.unit_id != task.assigned_worker:
        raise DispatchError("selected worker does not match the task's assigned_worker")
    if require_strict and worker.control_mode != "strict":
        raise DispatchError("strict control policy requires a strict-verified worker")
    if machine_id and worker.machine_affinity and machine_id not in worker.machine_affinity:
        raise DispatchError("worker is not permitted on this machine")
    identity = worker.identity
    return replace(task, assigned_worker=identity.unit_id,
                   maker_identity={"unit_id": identity.unit_id,
                                   "runtime": identity.runtime,
                                   "model": identity.model})


def eligible_workers(registry: WorkerRegistry, *, machine_id: str | None = None,
                     require_strict: bool = False,
                     role: str | None = None,
                     capacity: Mapping[str, CapacityObservation] | None = None,
                     now: float | None = None,
                     required_capability_floor: str | None = None,
                     capability_order: tuple[str, ...] = ()) -> tuple[WorkerDefinition, ...]:
    """Return active, locally eligible workers in registry order."""
    result: list[WorkerDefinition] = []
    current = time.time() if now is None else now
    if required_capability_floor is not None:
        configured_order = capability_order or registry.capability_order
        if not configured_order or len(set(configured_order)) != len(configured_order):
            raise DispatchError("capability_order must be a unique configured ordering")
        if required_capability_floor not in configured_order:
            raise DispatchError("required capability floor is absent from capability_order")
        floor_rank = configured_order.index(required_capability_floor)
    else:
        configured_order = capability_order
        floor_rank = -1
    for unit_id in registry.active:
        worker = registry.resolve(unit_id)
        if required_capability_floor is not None:
            if worker.identity.capability_floor not in configured_order:
                continue
            if configured_order.index(worker.identity.capability_floor) < floor_rank:
                continue
        if capacity is not None:
            observation = capacity.get(unit_id)
            if observation is None or not observation.eligible(now=current):
                continue
        if role is not None and worker.identity.role != role:
            continue
        if machine_id and worker.machine_affinity and machine_id not in worker.machine_affinity:
            continue
        if require_strict and worker.control_mode != "strict":
            continue
        result.append(worker)
    if role is not None:
        policy = registry.policy_for_role(role)
        if policy and policy.model_priority:
            rank = {model: index for index, model in enumerate(policy.model_priority)}
            result.sort(key=lambda worker: rank.get(worker.identity.model, len(rank)))
    return tuple(result)


def apply_fast_tier_restrictions(
    capacity: Mapping[str, CapacityObservation], classifications: Mapping[str, str],
) -> dict[str, CapacityObservation]:
    """Apply only restrictive fast-tier hints; model output can never grant capacity."""
    allowed = {"ok", "capacity", "quota", "auth", "crash", "timeout"}
    result = dict(capacity)
    for worker_id, kind in classifications.items():
        if worker_id not in result:
            raise DispatchError(f"fast-tier capacity hint names unknown worker: {worker_id}")
        if kind not in allowed:
            raise DispatchError("fast-tier capacity hint has an unknown classification")
        observation = result[worker_id]
        if kind == "quota":
            result[worker_id] = replace(observation, quota_remaining=0)
        elif kind in {"auth", "crash", "timeout"}:
            result[worker_id] = replace(observation, available=False)
    return result


def worker_readiness(registry: WorkerRegistry, tasks: tuple[TaskRecord, ...], *,
                     capacity: Mapping[str, CapacityObservation] | None = None,
                     machine_id: str | None = None, require_strict: bool = False,
                     role: str | None = None, now: float | None = None,
                     required_capability_floor: str | None = None) -> tuple[WorkerReadiness, ...]:
    """Report defined/active/available/eligible/assigned dimensions per worker."""
    current = time.time() if now is None else now
    eligible = {worker.identity.unit_id for worker in eligible_workers(
        registry, machine_id=machine_id, require_strict=require_strict, role=role,
        capacity=capacity, now=current, required_capability_floor=required_capability_floor,
    )}
    assigned: dict[str, list[str]] = {}
    for task in tasks:
        if (task.assigned_worker and task.state in {
            TaskState.CLAIMED, TaskState.IN_PROGRESS, TaskState.NEEDS_REVIEW
        }):
            assigned.setdefault(task.assigned_worker, []).append(task.task_id)
    report: list[WorkerReadiness] = []
    for unit_id in registry.defined:
        active = unit_id in registry.active
        observation = capacity.get(unit_id) if capacity is not None else None
        availability = ("unknown" if observation is None else
                        "available" if observation.eligible(now=current) else "unavailable")
        reasons: list[str] = []
        if not active:
            reasons.append("worker is defined but inactive")
        elif unit_id not in eligible:
            reasons.append("worker does not meet current routing policy")
        if availability == "unknown":
            reasons.append("no capacity observation supplied")
        elif availability == "unavailable":
            reasons.append("capacity observation is unavailable, stale, exhausted, or cooling down")
        report.append(WorkerReadiness(
            worker_id=unit_id, defined=True, active=active, availability=availability,
            eligible=unit_id in eligible, assigned_tasks=tuple(sorted(assigned.get(unit_id, ()))),
            reasons=tuple(reasons),
        ))
    return tuple(report)


def identity_snapshot(identity: WorkerIdentity) -> dict[str, str]:
    """Return the attribution dimensions needed for immutable audit records."""
    return {"unit_id": identity.unit_id, "runtime": identity.runtime, "model": identity.model}
