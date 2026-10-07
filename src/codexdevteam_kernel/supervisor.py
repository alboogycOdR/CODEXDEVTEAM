"""Lease-fenced, bounded supervision cycles for safe pending-task dispatch."""

from dataclasses import dataclass, field, replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
from threading import Event
from datetime import datetime, timezone
from typing import Callable, Mapping, Protocol

from .dispatch import (CapacityObservation, DispatchError, TaskClassPolicy,
                       apply_fast_tier_restrictions, eligible_workers)
from .control_queue import drain_control_outbox
from .gate import GateResult
from .health import StagnationSample, stale_signal
from .host_commit import (CommitLimits, HostCommitResult, QuiescenceProof,
                          RefusalReason, archive_refused_control_outbox, host_commit,
                          quarantine_out_of_scope_changes, validate_owned_retry_worktree)
from .memory import EvidenceMemory, FactInjection, render_fact_injection
from .process_identity import ProcessIdentity, observe_process_identity
from .protocol import TaskRecord
from .registry import WorkerRegistry
from .runtime import InvocationRequest, InvocationResult, request_for_worker
from .fast_tier import FastTierRunner
from .gate import GateRunner
from .lease_guard import HeadLeaseGuard
from .review import parse_review_verdict
from .state import HeadLease, LeaseError, StateStore
from .tasks import TaskState
from .territory import normalize_repo_path
from .worktrees import GitWorktreeManager


class InvocationAdapter(Protocol):
    def invoke(self, request: InvocationRequest) -> InvocationResult: ...


class EscalationNotifier(Protocol):
    """Transport adapter; notification_id is the stable transport idempotency key."""

    def notify(self, notification: Mapping[str, object]) -> bool: ...


@dataclass(frozen=True, slots=True)
class SupervisorPolicy:
    role: str = "implementation"
    require_strict: bool = True
    require_capacity_observation: bool = True
    machine_id: str | None = None
    required_capability_floor: str | None = None
    task_class_policy: TaskClassPolicy | None = None
    invocation_stale_after_seconds: float = 60.0
    plan_archive_interval_seconds: float | None = None
    ignored_paths_allowlist: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.role, str) or not self.role.strip():
            raise ValueError("supervisor role is required")
        if not isinstance(self.require_strict, bool) or not isinstance(
            self.require_capacity_observation, bool
        ):
            raise ValueError("supervisor safety policies must be boolean")
        if (self.required_capability_floor is not None
                and (not isinstance(self.required_capability_floor, str)
                     or not self.required_capability_floor.strip())):
            raise ValueError("required_capability_floor must be null or non-empty")
        if (self.task_class_policy is not None
                and not isinstance(self.task_class_policy, TaskClassPolicy)):
            raise ValueError("task_class_policy must be a TaskClassPolicy or null")
        if (isinstance(self.invocation_stale_after_seconds, bool)
                or not isinstance(self.invocation_stale_after_seconds, (int, float))
                or not math.isfinite(self.invocation_stale_after_seconds)
                or self.invocation_stale_after_seconds <= 0):
            raise ValueError("invocation_stale_after_seconds must be finite and positive")
        if (self.plan_archive_interval_seconds is not None
                and (isinstance(self.plan_archive_interval_seconds, bool)
                     or not isinstance(self.plan_archive_interval_seconds, (int, float))
                     or not math.isfinite(self.plan_archive_interval_seconds)
                     or self.plan_archive_interval_seconds <= 0)):
            raise ValueError("plan_archive_interval_seconds must be null or finite and positive")
        if (not isinstance(self.ignored_paths_allowlist, tuple)
                or any(not isinstance(path, str) or not path.strip()
                       for path in self.ignored_paths_allowlist)):
            raise ValueError("ignored_paths_allowlist must be a tuple of non-empty path patterns")
        for path in self.ignored_paths_allowlist:
            normalize_repo_path(path)


@dataclass(frozen=True, slots=True)
class SupervisorCycleResult:
    cycle_id: str
    mode: str
    assignments: tuple[str, ...]
    deferred: Mapping[str, str]
    health_actions: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    archived_task_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskInvocationCycleResult:
    task_id: str
    worker_id: str
    worktree_path: str
    invocation: InvocationResult
    control_applied: tuple[str, ...] = ()
    control_rejected: tuple[str, ...] = ()
    host_commit: HostCommitResult | None = None


@dataclass(frozen=True, slots=True)
class TaskCheckerCycleResult:
    task_id: str
    checker_id: str
    invocation: InvocationResult


@dataclass(frozen=True, slots=True)
class MakerCloseoutResult:
    """Durable maker, gate, and independent review evidence for one task."""

    maker: TaskInvocationCycleResult
    gate: GateResult | None
    checker: TaskCheckerCycleResult | None
    review_applied: bool


@dataclass(frozen=True, slots=True)
class SupervisorLaunchCycleResult:
    dispatch: SupervisorCycleResult
    makers: tuple[TaskInvocationCycleResult, ...]
    notification_result: "NotificationCycleResult | None" = None


@dataclass(frozen=True, slots=True)
class NotificationCycleResult:
    claimed: tuple[str, ...]
    delivered: tuple[str, ...]
    retryable: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContinuousSupervisorResult:
    cycles_completed: int
    stop_reason: str
    last_cycle: SupervisorLaunchCycleResult | None = None


class Supervisor:
    """Run one bounded dispatch cycle; runtime launches remain separate effects.

    A cycle only claims tasks through StateStore's exclusive HEAD lease. It does
    not launch a provider process or change a task's review state. Parked mode
    is the durable default, and dispatch requires strict-verified workers and
    fresh capacity observations by default.
    """

    PRIORITY = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    ACTIVE = {TaskState.CLAIMED, TaskState.IN_PROGRESS, TaskState.NEEDS_REVIEW}

    def __init__(self, store: StateStore, registry: WorkerRegistry,
                 policy: SupervisorPolicy | None = None):
        self.store = store
        self.registry = registry
        self.policy = policy or SupervisorPolicy()

    def run_dispatch_cycle(self, lease: HeadLease, *, cycle_id: str,
                           capacity: Mapping[str, CapacityObservation] | None,
                           fast_tier_runner: FastTierRunner | None = None,
                           fast_tier_worker_id: str | None = None,
                           fast_tier_logs: Mapping[str, str] | None = None,
                           fast_tier_adapters: Mapping[str, InvocationAdapter] | None = None,
                           fast_tier_working_directory: str | Path | None = None,
                           project_root: str | Path | None = None,
                           max_assignments: int | None = None,
                           task_health_samples: Mapping[str, StagnationSample] | None = None,
                           stagnation_policy: Mapping[str, int] | None = None,
                           now: float | None = None) -> SupervisorCycleResult:
        if not isinstance(cycle_id, str) or not cycle_id.strip():
            raise ValueError("cycle_id is required")
        if (max_assignments is not None
                and (isinstance(max_assignments, bool)
                     or not isinstance(max_assignments, int) or max_assignments < 1)):
            raise ValueError("max_assignments must be a positive integer or null")
        current = time.time() if now is None else now
        # A prior HEAD can lose its lease after terminating a maker. Verify its
        # one-use sidecar before stagnation/dispatch decides whether the task is held.
        self.store.recover_invocation_cancellation_receipts(lease, now=now)
        # A prior HEAD may have committed state and crashed before refreshing
        # PLAN. Recover that intent before dispatch decisions or side effects.
        self.store.apply_pending_plan_projections(lease, now=now)
        mode = self.store.get_supervisor_mode()
        if mode["mode"] != "running":
            return SupervisorCycleResult(cycle_id, mode["mode"], (), {})
        if self.policy.plan_archive_interval_seconds is not None and project_root is None:
            raise ValueError("scheduled PLAN archival requires project_root")
        if self.policy.require_capacity_observation and capacity is None:
            raise ValueError("this supervisor policy requires capacity observations")
        capacity_snapshot = (capacity if self.policy.require_capacity_observation
                             or capacity is not None else None)
        fast_inputs = (fast_tier_worker_id, fast_tier_logs, fast_tier_adapters,
                       fast_tier_working_directory)
        if fast_tier_runner is None and any(item is not None for item in fast_inputs):
            raise ValueError("fast-tier dispatch inputs require a configured FastTierRunner")
        if fast_tier_runner is not None:
            if (not isinstance(fast_tier_worker_id, str) or not fast_tier_worker_id.strip()
                    or not isinstance(fast_tier_logs, Mapping)
                    or not isinstance(fast_tier_adapters, Mapping)
                    or fast_tier_working_directory is None):
                raise ValueError("fast-tier worker, logs, adapters, and working directory are required")
            if capacity_snapshot is None:
                raise ValueError("fast-tier capacity hints require independent capacity observations")
            classifications = {}
            for observed_worker, run_log in sorted(fast_tier_logs.items()):
                if observed_worker not in capacity_snapshot:
                    raise ValueError("fast-tier logs must match independently observed workers")
                digest = hashlib.sha256(f"{cycle_id}\0{observed_worker}".encode()).hexdigest()[:24]
                result = fast_tier_runner.classify_run_log(
                    lease, job_id=f"capacity-{digest}", worker_id=fast_tier_worker_id,
                    run_log=run_log, adapters=fast_tier_adapters,
                    working_directory=fast_tier_working_directory, now=now,
                )
                if result.classification is not None:
                    classifications[observed_worker] = result.classification["kind"]
            capacity_snapshot = apply_fast_tier_restrictions(capacity_snapshot, classifications)
        health_actions: dict[str, Mapping[str, object]] = {}
        if task_health_samples is not None:
            if not isinstance(task_health_samples, Mapping):
                raise ValueError("task_health_samples must be a mapping")
            if stagnation_policy is not None and not isinstance(stagnation_policy, Mapping):
                raise ValueError("stagnation_policy must be a mapping")
            if (not all(isinstance(task_id, str) for task_id in task_health_samples)
                    or not all(isinstance(sample, StagnationSample)
                               for sample in task_health_samples.values())):
                raise ValueError("health samples must map task IDs to StagnationSample values")
            for task_id in sorted(task_health_samples):
                sample = task_health_samples[task_id]
                health = self.store.evaluate_stagnation(
                    lease, task_id, sample,
                    event_id=f"supervisor:{cycle_id}:health:{task_id}",
                    policy=dict(stagnation_policy) if stagnation_policy is not None else None,
                    now=now,
                )
                notification_id = None
                if health["action"] == "escalate":
                    escalation = self.store.record_escalation(
                        lease, escalation_key=f"{task_id}:stagnation", task_id=task_id,
                        severity="high",
                        message="The task stagnation circuit breaker opened after repeated no-progress or denial samples.",
                        event_id=f"supervisor:{cycle_id}:stagnation-escalation:{task_id}",
                        now=now,
                    )
                    notification_id = escalation["notification_id"]
                health_actions[task_id] = {**health, "notification_id": notification_id}
        for invocation in self.store.task_invocation_liveness(state="running"):
            signal = stale_signal(
                datetime.fromtimestamp(invocation["heartbeat_at"], timezone.utc),
                now=datetime.fromtimestamp(current, timezone.utc),
                stale_after_seconds=self.policy.invocation_stale_after_seconds,
            )
            if not signal.stale:
                continue
            identity = None
            if invocation["process_pid"] is None:
                process_observation = "identity_not_recorded"
            else:
                try:
                    identity = ProcessIdentity(
                        invocation["process_pid"], invocation["process_start_token"],
                        invocation["process_group_id"],
                        invocation.get("process_containment_ref"),
                    )
                    process_observation = observe_process_identity(identity).value
                except (TypeError, ValueError):
                    process_observation = "unverifiable"
                    identity = None
            identity_fingerprint = (
                hashlib.sha256(identity.start_token.encode("utf-8")).hexdigest()
                if identity is not None else None
            )
            observation_payload = {
                "type": "task.invocation_process_observed",
                "task_id": invocation["task_id"],
                "invocation_id": invocation["invocation_id"],
                "process_pid": identity.pid if identity is not None else None,
                "process_group_id": (identity.process_group_id
                                     if identity is not None else None),
                "identity_fingerprint_sha256": identity_fingerprint,
                "observation": process_observation,
                "diagnostic_only": True,
            }
            observation_fingerprint = hashlib.sha256(json.dumps(
                observation_payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")).hexdigest()[:32]
            observation_event_id = (
                f"process-observation:{invocation['invocation_id']}:"
                f"{observation_fingerprint}"
            )
            self.store.record_event(lease, observation_event_id,
                                    observation_payload, now=now)
            escalation = self.store.record_escalation(
                lease,
                escalation_key=(f"{invocation['task_id']}:stale-invocation:"
                                f"{invocation['invocation_id']}"),
                task_id=invocation["task_id"], severity="high",
                message=("Maker invocation heartbeat is stale; verify process-tree "
                         "termination before authorizing redispatch."),
                event_id=f"supervisor:{cycle_id}:stale:{invocation['invocation_id']}",
                now=now,
            )
            health_actions[f"invocation:{invocation['invocation_id']}"] = {
                "task_id": invocation["task_id"], "action": "escalate_stale_invocation",
                "stale": True, "age_seconds": signal.age_seconds,
                "reason": signal.reason,
                "process_observation": process_observation,
                "process_observation_event_id": observation_event_id,
                "notification_id": escalation["notification_id"],
            }
        archived_task_ids: tuple[str, ...] = ()
        archive_interval = self.policy.plan_archive_interval_seconds
        if archive_interval is not None:
            last_archive = self.store.maintenance_completed_at("plan_archive")
            if last_archive is None or current - last_archive >= archive_interval:
                plan_path = Path(project_root) / "PLAN.md"
                if plan_path.is_symlink() or not plan_path.is_file():
                    raise ValueError("scheduled PLAN archival requires a regular project PLAN.md")
                expected_plan_sha256 = hashlib.sha256(plan_path.read_bytes()).hexdigest()
                archived_task_ids = self.store.archive_older_plan_tasks(
                    lease, project_root,
                    event_id=f"supervisor:{cycle_id}:plan-archive",
                    expected_plan_sha256=expected_plan_sha256, now=now,
                )
                self.store.record_maintenance_completion(
                    lease, "plan_archive",
                    event_id=f"supervisor:{cycle_id}:plan-archive-completed", now=now,
                )
        tasks = self.store.list_tasks()
        completed = self.store.completed_dependency_ids()
        occupied = {task.assigned_worker for task in tasks
                    if task.state in self.ACTIVE and task.assigned_worker}
        assignments: list[str] = []
        deferred: dict[str, str] = {}
        pending = sorted((task for task in tasks if task.state is TaskState.PENDING),
                         key=lambda task: (self.PRIORITY[task.priority], task.task_id))
        for task in pending:
            if max_assignments is not None and len(assignments) >= max_assignments:
                deferred[task.task_id] = "cycle assignment limit reached"
                continue
            unresolved = sorted(set(task.depends_on) - completed)
            if unresolved:
                deferred[task.task_id] = "dependencies are not complete: " + ", ".join(unresolved)
                continue
            if task.task_class is not None and self.policy.task_class_policy is None:
                raise DispatchError("task has a class but supervisor has no task-class policy")
            class_floor = (self.policy.task_class_policy.floor_for(task.task_class)
                           if self.policy.task_class_policy is not None else None)
            task_role = (self.policy.task_class_policy.role_for(
                task.task_class, fallback=self.policy.role)
                if self.policy.task_class_policy is not None else self.policy.role)
            floors = [floor for floor in
                      (self.policy.required_capability_floor, class_floor) if floor is not None]
            task_floor = floors[0] if floors else None
            if len(set(floors)) > 1:
                order = self.registry.capability_order
                if not order or any(floor not in order for floor in floors):
                    raise DispatchError("combined task-class and supervisor floors require a "
                                        "complete capability_order")
                task_floor = max(floors, key=order.index)
            workers = eligible_workers(
                self.registry, machine_id=self.policy.machine_id,
                require_strict=self.policy.require_strict, role=task_role,
                capacity=capacity_snapshot, now=current,
                required_capability_floor=task_floor,
            )
            if not workers:
                deferred[task.task_id] = "no worker passed strict role, capability, and capacity policy"
                continue
            candidates_by_id = {worker.identity.unit_id: worker for worker in workers}
            candidate_ids = ([task.assigned_worker] if task.assigned_worker else
                             [worker.identity.unit_id for worker in workers])
            candidate = next((candidates_by_id[unit_id] for unit_id in candidate_ids
                              if unit_id in candidates_by_id and unit_id not in occupied), None)
            if candidate is None:
                deferred[task.task_id] = "no eligible unoccupied worker is available"
                continue
            claimed = self.store.assign_pending_task(
                lease, task.task_id, candidate.identity.unit_id, self.registry,
                event_id=f"supervisor:{cycle_id}:assign:{task.task_id}",
                machine_id=self.policy.machine_id,
                require_strict=self.policy.require_strict,
                require_supervisor_running=True,
                project_root=project_root,
                now=now,
            )
            assignments.append(claimed.task_id)
            occupied.add(candidate.identity.unit_id)
        return SupervisorCycleResult(cycle_id, "running", tuple(assignments), deferred,
                                     health_actions, archived_task_ids)

    def run_dispatch_and_launch_cycle(
            self, lease: HeadLease, *, cycle_id: str,
            capacity: Mapping[str, CapacityObservation] | None,
            task_prompts: Mapping[str, str],
            invocation_ids: Mapping[str, str],
            base_ref: str, worktrees: GitWorktreeManager,
            adapters: Mapping[str, InvocationAdapter],
            max_tasks: int = 1,
            project_root: str | Path | None = None,
            state_db_path: str | Path | None = None,
            worktree_copy: tuple[str, ...] = (),
            allowed_environment: tuple[str, ...] = (),
            task_health_samples: Mapping[str, StagnationSample] | None = None,
            stagnation_policy: Mapping[str, int] | None = None,
            fast_tier_runner: FastTierRunner | None = None,
            fast_tier_worker_id: str | None = None,
            fast_tier_logs: Mapping[str, str] | None = None,
            fast_tier_adapters: Mapping[str, InvocationAdapter] | None = None,
            fast_tier_working_directory: str | Path | None = None,
            notifier: EscalationNotifier | None = None,
            notification_limit: int = 10,
            notification_claim_seconds: float = 120.0,
            notification_retry_after_seconds: float = 60.0,
            now: float | None = None) -> SupervisorLaunchCycleResult:
        """Dispatch and synchronously launch a bounded batch of maker tasks.

        All prompt and invocation identity inputs are validated before dispatch
        can claim work. Maker CONTROL remains queued for the caller to gate and
        drain through ``finalize_maker_gate``.
        """
        if not isinstance(task_prompts, Mapping) or not isinstance(invocation_ids, Mapping):
            raise ValueError("task_prompts and invocation_ids must be mappings")
        if (isinstance(max_tasks, bool) or not isinstance(max_tasks, int) or max_tasks < 1):
            raise ValueError("max_tasks must be a positive integer")
        if not isinstance(base_ref, str) or not base_ref.strip():
            raise ValueError("base_ref is required")
        if not isinstance(worktrees, GitWorktreeManager):
            raise ValueError("worktrees must be a GitWorktreeManager")
        if not isinstance(adapters, Mapping):
            raise ValueError("adapters must be a mapping")
        if notifier is not None and not callable(getattr(notifier, "notify", None)):
            raise ValueError("notifier must implement notify(notification)")
        if (isinstance(notification_limit, bool) or not isinstance(notification_limit, int)
                or notification_limit < 1):
            raise ValueError("notification_limit must be a positive integer")
        for name, value in (("notification_claim_seconds", notification_claim_seconds),
                            ("notification_retry_after_seconds", notification_retry_after_seconds)):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be finite and positive")
        pending_ids = {task.task_id for task in self.store.list_tasks()
                       if task.state is TaskState.PENDING}
        missing_prompts = sorted(task_id for task_id in pending_ids
                                 if not isinstance(task_prompts.get(task_id), str)
                                 or not task_prompts[task_id].strip())
        missing_invocations = sorted(task_id for task_id in pending_ids
                                     if not isinstance(invocation_ids.get(task_id), str)
                                     or not re.fullmatch(
                                         r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}",
                                         invocation_ids[task_id]))
        if missing_prompts or missing_invocations:
            details = []
            if missing_prompts:
                details.append("missing prompts: " + ", ".join(missing_prompts))
            if missing_invocations:
                details.append("missing or invalid invocation IDs: " +
                               ", ".join(missing_invocations))
            raise ValueError("launch inputs must cover all pending tasks before dispatch; "
                             + "; ".join(details))
        configured_ids = [invocation_ids[task_id] for task_id in pending_ids]
        if len(configured_ids) != len(set(configured_ids)):
            raise ValueError("invocation IDs must be unique across pending tasks")

        dispatch = self.run_dispatch_cycle(
            lease, cycle_id=cycle_id, capacity=capacity,
            project_root=project_root, max_assignments=max_tasks,
            task_health_samples=task_health_samples,
            stagnation_policy=stagnation_policy,
            fast_tier_runner=fast_tier_runner,
            fast_tier_worker_id=fast_tier_worker_id,
            fast_tier_logs=fast_tier_logs,
            fast_tier_adapters=fast_tier_adapters,
            fast_tier_working_directory=fast_tier_working_directory, now=now,
        )
        notification_result = (self.deliver_escalation_notifications(
            lease, notifier, limit=notification_limit,
            claim_seconds=notification_claim_seconds,
            retry_after_seconds=notification_retry_after_seconds,
            now=now) if notifier is not None else None)
        makers: list[TaskInvocationCycleResult] = []
        for task_id in dispatch.assignments:
            makers.append(self.invoke_claimed_task(
                lease, task_id, invocation_id=invocation_ids[task_id],
                prompt=task_prompts[task_id], base_ref=base_ref,
                worktrees=worktrees, adapters=adapters,
                project_root=project_root,
                state_db_path=state_db_path, worktree_copy=worktree_copy,
                allowed_environment=allowed_environment,
                defer_control_drain=True, now=now,
            ))
        return SupervisorLaunchCycleResult(dispatch, tuple(makers), notification_result)

    def run_continuous(
            self, lease: HeadLease, *,
            cycle_inputs: Callable[[int], Mapping[str, object]],
            cycle_id_prefix: Callable[[int], str],
            stop_event: Event, interval_seconds: float = 10.0,
            lease_ttl_seconds: float = 30.0,
            max_cycles: int | None = None,
            notifier: EscalationNotifier | None = None,
            notification_limit: int = 10,
            notification_claim_seconds: float = 120.0,
            notification_retry_after_seconds: float = 60.0,
            on_cycle: Callable[[SupervisorLaunchCycleResult], None] | None = None,
            ) -> ContinuousSupervisorResult:
        """Run bounded dispatch-and-launch cycles until stopped or parked.

        Runtime operations remain synchronous and bounded by their invocation
        timeout. Setting ``stop_event`` stops subsequent cycles; a running
        invocation finishes or reaches its configured timeout first. Callers
        can also park through HEAD mode to prevent later claims. After each
        maker cycle, this loop requires every launched task to have a durable
        done, blocked, or pending disposition. It records and stops on missing
        gate/review closeout rather than dispatching another batch.
        """
        if not callable(cycle_inputs) or not callable(cycle_id_prefix):
            raise ValueError("cycle_inputs and cycle_id_prefix must be callable")
        if not isinstance(stop_event, Event):
            raise ValueError("stop_event must be a threading.Event")
        if (isinstance(interval_seconds, bool)
                or not isinstance(interval_seconds, (int, float))
                or not math.isfinite(interval_seconds) or interval_seconds <= 0):
            raise ValueError("interval_seconds must be finite and positive")
        if (isinstance(lease_ttl_seconds, bool)
                or not isinstance(lease_ttl_seconds, (int, float))
                or not math.isfinite(lease_ttl_seconds) or lease_ttl_seconds <= 0):
            raise ValueError("lease_ttl_seconds must be finite and positive")
        if (max_cycles is not None
                and (isinstance(max_cycles, bool)
                     or not isinstance(max_cycles, int) or max_cycles < 1)):
            raise ValueError("max_cycles must be a positive integer or null")
        if on_cycle is not None and not callable(on_cycle):
            raise ValueError("on_cycle must be callable or null")
        if notifier is not None and not callable(getattr(notifier, "notify", None)):
            raise ValueError("notifier must implement notify(notification)")
        if (isinstance(notification_limit, bool) or not isinstance(notification_limit, int)
                or notification_limit < 1):
            raise ValueError("notification_limit must be a positive integer")
        for name, value in (("notification_claim_seconds", notification_claim_seconds),
                            ("notification_retry_after_seconds", notification_retry_after_seconds)):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be finite and positive")

        allowed_inputs = {
            "capacity", "task_prompts", "invocation_ids", "base_ref", "worktrees",
            "adapters", "max_tasks", "project_root", "state_db_path", "worktree_copy",
            "allowed_environment", "task_health_samples", "stagnation_policy",
            "fast_tier_runner", "fast_tier_worker_id", "fast_tier_logs",
            "fast_tier_adapters", "fast_tier_working_directory", "now",
        }
        required_inputs = {"capacity", "task_prompts", "invocation_ids", "base_ref",
                           "worktrees", "adapters"}
        last_cycle = None
        completed = 0
        reason = "stop_requested"
        # Legacy deterministic callers may supply synthetic timestamps. Real
        # acquired leases use the host clock and get a keepalive across the
        # whole continuous lifecycle, including callbacks and idle waits.
        guard = (HeadLeaseGuard(
            self.store, lease, ttl_seconds=lease_ttl_seconds,
            renew_interval_seconds=min(5.0, lease_ttl_seconds / 3.0),
        ) if lease.expires_at > time.time() else None)
        if guard is not None:
            guard.__enter__()
        try:
            while not stop_event.is_set():
                if guard is not None:
                    guard.raise_if_lost()
                if max_cycles is not None and completed >= max_cycles:
                    reason = "max_cycles"
                    break
                tick = completed + 1
                inputs = cycle_inputs(tick)
                if not isinstance(inputs, Mapping) or not all(
                        isinstance(key, str) for key in inputs):
                    raise ValueError("cycle_inputs must return a string-keyed mapping")
                missing = required_inputs - set(inputs)
                unknown = set(inputs) - allowed_inputs
                if missing or unknown:
                    details = []
                    if missing:
                        details.append("missing " + ", ".join(sorted(missing)))
                    if unknown:
                        details.append("unknown " + ", ".join(sorted(unknown)))
                    raise ValueError("invalid cycle inputs: " + "; ".join(details))
                prefix = cycle_id_prefix(tick)
                if not isinstance(prefix, str) or not prefix.strip():
                    raise ValueError("cycle_id_prefix must return non-empty text")
                preexisting_active = [
                    task for task in self.store.list_tasks()
                    if task.state in {TaskState.CLAIMED, TaskState.NEEDS_REVIEW}
                ]
                if preexisting_active:
                    unresolved = [{"task_id": task.task_id, "state": task.state.value}
                                  for task in preexisting_active]
                    self.store.record_event(
                        lease, f"continuous-preexisting-active:{prefix}:{tick}",
                        {"type": "supervisor.continuous_preexisting_active_tasks",
                         "cycle_id": f"{prefix}:{tick}", "tasks": unresolved},
                        now=inputs.get("now"),
                    )
                    reason = "active_tasks_require_recovery"
                    break
                result = self.run_dispatch_and_launch_cycle(
                    lease, cycle_id=f"{prefix}:{tick}", notifier=notifier,
                    notification_limit=notification_limit,
                    notification_claim_seconds=notification_claim_seconds,
                    notification_retry_after_seconds=notification_retry_after_seconds,
                    **dict(inputs),
                )
                completed += 1
                last_cycle = result
                if on_cycle is not None:
                    try:
                        on_cycle(result)
                    except Exception as exc:
                        failed_tasks = []
                        for maker in result.makers:
                            task = self.store.get_task(maker.task_id)
                            failed_tasks.append({
                                "task_id": maker.task_id,
                                "state": "missing" if task is None else task.state.value,
                            })
                        failure_type = type(exc).__name__
                        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", failure_type):
                            failure_type = "Exception"
                        self.store.record_event(
                            lease, f"continuous-closeout-failed:{prefix}:{tick}",
                            {"type": "supervisor.continuous_closeout_failed",
                             "cycle_id": result.dispatch.cycle_id,
                             "failure_type": failure_type, "tasks": failed_tasks},
                            now=inputs.get("now"),
                        )
                        reason = "closeout_failed"
                        break
                if guard is not None:
                    guard.raise_if_lost()
                unresolved = []
                for maker in result.makers:
                    task = self.store.get_task(maker.task_id)
                    if task is None:
                        unresolved.append({"task_id": maker.task_id, "state": "missing"})
                    elif task.state not in {
                            TaskState.DONE, TaskState.BLOCKED, TaskState.PENDING}:
                        unresolved.append({"task_id": maker.task_id,
                                           "state": task.state.value})
                if unresolved:
                    self.store.record_event(
                        lease, f"continuous-closeout-incomplete:{prefix}:{tick}",
                        {"type": "supervisor.continuous_closeout_incomplete",
                         "cycle_id": result.dispatch.cycle_id,
                         "tasks": unresolved},
                        now=inputs.get("now"),
                    )
                    reason = "closeout_incomplete"
                    break
                if stop_event.is_set():
                    reason = "stop_requested"
                    break
                if result.dispatch.mode != "running":
                    reason = "parked"
                    break
                if max_cycles is not None and completed >= max_cycles:
                    reason = "max_cycles"
                    break
                if stop_event.wait(interval_seconds):
                    reason = "stop_requested"
                    break
        finally:
            if guard is not None:
                guard.close()
        return ContinuousSupervisorResult(completed, reason, last_cycle)

    def record_gate_outcome(self, lease: HeadLease, gate: GateResult, *,
                            attempt_event_id: str, now: float | None = None) -> str | None:
        """Journal every completed gate; register passed receipts for checker use."""
        self.store.record_gate_attempt(lease, gate, event_id=attempt_event_id, now=now)
        test_run = gate.test_run_result
        if test_run is not None and test_run.passed:
            if test_run.sha.lower() != gate.sha.lower():
                raise ValueError("gate test evidence SHA differs from the gate result")
            self.store.register_test_run(
                lease, gate.task_id, test_run,
                event_id=f"{attempt_event_id}:test-run", now=now,
            )
        if gate.status != "passed":
            return None
        return self.store.record_gate_result(lease, gate, now=now)

    def deliver_escalation_notifications(
            self, lease: HeadLease, notifier: EscalationNotifier, *,
            limit: int = 10, claim_seconds: float = 120.0,
            retry_after_seconds: float = 60.0,
            now: float | None = None) -> NotificationCycleResult:
        """Deliver a bounded outbox batch; transport retries are at-least-once.

        The adapter receives the stable notification ID and should use it as
        its idempotency key. Provider errors are reduced to a fixed code before
        journaling so transport output cannot leak secrets into state events.
        """
        if not callable(getattr(notifier, "notify", None)):
            raise ValueError("notifier must implement notify(notification)")
        notifications = self.store.claim_escalation_notifications(
            lease, limit=limit, claim_seconds=claim_seconds, now=now,
        )
        delivered: list[str] = []
        retryable: list[str] = []
        for notification in notifications:
            notification_id = notification["notification_id"]
            public_payload = {
                "notification_id": notification_id,
                "escalation_key": notification["escalation_key"],
                "task_id": notification["task_id"],
                "severity": notification["severity"],
                "message": notification["message"],
                "notification_count": notification["notification_count"],
                "attempt": notification["attempt_count"],
            }
            error_code = None
            try:
                was_delivered = notifier.notify(public_payload) is True
                if not was_delivered:
                    error_code = "NOTIFIER_REJECTED"
            except Exception:
                was_delivered = False
                error_code = "NOTIFIER_ERROR"
            self.store.acknowledge_escalation_notification(
                lease, notification_id, notification["claim_token"],
                delivered=was_delivered,
                event_id=f"notification-ack:{notification_id}:{notification['attempt_count']}",
                error_code=error_code, retry_after_seconds=retry_after_seconds, now=now,
            )
            (delivered if was_delivered else retryable).append(notification_id)
        return NotificationCycleResult(
            tuple(item["notification_id"] for item in notifications),
            tuple(delivered), tuple(retryable),
        )

    def finalize_maker_gate(self, lease: HeadLease, cycle: TaskInvocationCycleResult,
                            gate: GateResult, *, attempt_event_id: str,
                            project_root: str | Path | None = None,
                            now: float | None = None) -> TaskInvocationCycleResult:
        """Register a gate for the maker's exact worktree SHA, then drain CONTROL."""
        if not isinstance(cycle, TaskInvocationCycleResult) or not isinstance(gate, GateResult):
            raise ValueError("maker cycle and gate result are required")
        if cycle.task_id != gate.task_id:
            raise ValueError("gate task does not match the maker cycle")
        if (cycle.host_commit is None or cycle.host_commit.status != "committed"
                or cycle.host_commit.sha is None
                or cycle.host_commit.sha.lower() != gate.sha.lower()):
            raise ValueError("gate requires the exact SHA published by a successful host commit")
        if cycle.control_applied or cycle.control_rejected:
            raise ValueError("maker CONTROL was already drained; defer draining until after the gate")
        worktree = Path(cycle.worktree_path).resolve(strict=True)
        if not worktree.is_dir():
            raise ValueError("maker worktree is unavailable")
        current = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False,
        )
        if current.returncode or current.stdout.strip().lower() != gate.sha.lower():
            raise ValueError("gate SHA does not match the maker worktree HEAD")
        self.record_gate_outcome(lease, gate, attempt_event_id=attempt_event_id, now=now)
        control = drain_control_outbox(
            self.store, lease, _control_outbox_path(worktree, cycle.invocation.invocation_id),
            project_root=project_root,
            expected_invocation_id=cycle.invocation.invocation_id, now=now,
        )
        task = self.store.get_task(gate.task_id)
        if gate.status == "passed" and task is not None and task.state is TaskState.IN_PROGRESS:
            self.store.transition_task_to_review_from_gate(
                lease, gate,
                event_id=f"gate-ready-for-review:{gate.task_id}:{gate.sha}",
                project_root=project_root, now=now,
            )
        return replace(cycle, control_applied=control["applied"],
                       control_rejected=control["rejected"])

    def closeout_maker_with_checker(
            self, lease: HeadLease, cycle: TaskInvocationCycleResult, *,
            gate_runner: GateRunner, base_ref: str,
            commands: Mapping[str, tuple[str, ...] | list[str] | None],
            checker_id: str,
            checker_prompt: str | Callable[[TaskRecord, GateResult], str],
            adapters: Mapping[str, InvocationAdapter],
            gate_attempt_event_id: str, checker_invocation_id: str,
            review_event_id: str, allowed_environment: tuple[str, ...] = (),
            gate_timeout_seconds: float = 1800.0,
            checker_timeout_seconds: float = 900.0,
            memory: EvidenceMemory | None = None,
            project_root: str | Path | None = None,
            now: float | None = None) -> MakerCloseoutResult:
        """Run the exact-SHA gate and independent checker/review in order.

        A refused or missing host commit is never sent to the gate. Failed gates
        and checker/review failures remain open for the continuous runner's
        closeout guard to stop and escalate. This method performs one review
        attempt; it does not silently rework or redispatch the maker.
        """
        if not isinstance(cycle, TaskInvocationCycleResult):
            raise ValueError("maker cycle is required")
        if not isinstance(gate_runner, GateRunner):
            raise ValueError("gate_runner must be a configured GateRunner")
        if not isinstance(base_ref, str) or not base_ref.strip():
            raise ValueError("base_ref is required")
        if not isinstance(checker_prompt, str) and not callable(checker_prompt):
            raise ValueError("checker_prompt must be text or a gate-bound prompt builder")
        for label, value in (("gate_attempt_event_id", gate_attempt_event_id),
                             ("checker_invocation_id", checker_invocation_id),
                             ("review_event_id", review_event_id)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} is required")
        task = self.store.get_task(cycle.task_id)
        if task is None:
            raise ValueError("maker task is missing from authoritative state")
        if (cycle.host_commit is None or cycle.host_commit.status != "committed"
                or not cycle.host_commit.sha):
            if task.state is TaskState.BLOCKED:
                return MakerCloseoutResult(cycle, None, None, False)
            raise ValueError("gate closeout requires a successful host commit")

        active_tasks = tuple(self.store.list_tasks())
        open_task_ids = tuple(sorted(
            item.task_id for item in active_tasks if item.state is not TaskState.DONE
        ))
        gate = gate_runner.run(
            task, cycle.worktree_path,
            expected_sha=cycle.host_commit.sha, base_ref=base_ref,
            commands=commands, allowed_environment=allowed_environment,
            timeout_seconds=gate_timeout_seconds, open_task_ids=open_task_ids,
            active_tasks=active_tasks,
        )
        finalized = self.finalize_maker_gate(
            lease, cycle, gate, attempt_event_id=gate_attempt_event_id,
            project_root=project_root, now=now,
        )
        if gate.status != "passed":
            return MakerCloseoutResult(finalized, gate, None, False)
        current = self.store.get_task(cycle.task_id)
        if current is None or current.state is not TaskState.NEEDS_REVIEW:
            return MakerCloseoutResult(finalized, gate, None, False)
        effective_checker_prompt = (checker_prompt(current, gate)
                                    if callable(checker_prompt) else checker_prompt)
        if not isinstance(effective_checker_prompt, str) or not effective_checker_prompt.strip():
            raise ValueError("gate-bound checker prompt must return non-empty text")
        checker = self.invoke_checker(
            lease, cycle.task_id, checker_id, gate=gate,
            prompt=effective_checker_prompt, working_directory=cycle.worktree_path,
            adapters=adapters, invocation_id=checker_invocation_id,
            timeout_seconds=checker_timeout_seconds,
            allowed_environment=allowed_environment, memory_injection=None, now=now,
        )
        applied = self.apply_checker_output(
            lease, checker, gate=gate, event_id=review_event_id, memory=memory,
            project_root=project_root, now=now,
        )
        return MakerCloseoutResult(finalized, gate, checker, applied)

    def invoke_checker(self, lease: HeadLease, task_id: str, checker_id: str, *,
                       gate: GateResult, prompt: str, working_directory: str | Path,
                       adapters: Mapping[str, InvocationAdapter],
                       invocation_id: str, timeout_seconds: float = 900.0,
                       allowed_environment: tuple[str, ...] = (),
                       memory_injection: FactInjection | None = None,
                       now: float | None = None) -> TaskCheckerCycleResult:
        """Launch a configured independent checker only after the SHA-bound gate.

        This records a checker invocation receipt; it never converts model output
        into a verdict or approves task completion.
        """
        if self.store.get_supervisor_mode()["mode"] != "running":
            raise ValueError("checker invocation requires supervisor running mode")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("checker prompt is required")
        if (not isinstance(invocation_id, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", invocation_id)
                or isinstance(timeout_seconds, bool)
                or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("invocation_id or timeout_seconds is invalid")
        task = self.store.get_task(task_id)
        if task is None or task.state is not TaskState.NEEDS_REVIEW:
            raise ValueError("only a task awaiting review can be checked")
        effective_prompt = prompt
        if memory_injection is not None:
            memory_context = render_fact_injection(memory_injection, task_id=task_id)
            if memory_context:
                effective_prompt += (
                    "\n\n" + memory_context + "\n"
                    "If a listed fact materially affected your review findings, include its exact "
                    "memory-fact:<fact-id> citation in evidence_refs. Do not cite unused facts."
                )
        if (gate.task_id != task_id or gate.status != "passed"
                or not self.store.has_registered_gate(
                    lease, task_id, gate.sha, gate.fingerprint, now=now)):
            raise ValueError("checker launch requires the unchanged HEAD-registered passed gate")
        cwd = Path(working_directory).resolve(strict=True)
        if not cwd.is_dir():
            raise ValueError("checker working directory must be a directory")
        head = subprocess.run(["git", "-C", str(cwd), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=False)
        cleanliness = subprocess.run(
            ["git", "-C", str(cwd), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True, text=True, check=False,
        )
        if (head.returncode or head.stdout.strip().lower() != gate.sha.lower()
                or cleanliness.returncode or cleanliness.stdout.strip()):
            raise ValueError("checker worktree must be clean and match the registered gate SHA")
        maker = task.maker_identity
        if maker is None:
            raise ValueError("task has no immutable maker identity snapshot")
        if checker_id not in self.registry.active:
            raise ValueError("checker must be an active configured worker")
        checker = self.registry.resolve(checker_id)
        identity = checker.identity
        if identity.role not in {"reviewer", "judgment"}:
            raise ValueError("checker worker must have reviewer or judgment role")
        if identity.unit_id == maker["unit_id"]:
            raise ValueError("maker and checker units must differ")
        if identity.runtime == maker["runtime"] and identity.model == maker["model"]:
            raise ValueError("checker must use an independent runtime/model")
        adapter = adapters.get(identity.runtime)
        if adapter is None or not callable(getattr(adapter, "invoke", None)):
            raise ValueError(f"no invocation adapter for runtime {identity.runtime}")
        request = request_for_worker(
            self.registry, checker_id, invocation_id=invocation_id, task_id=task_id,
            purpose="checker", prompt=effective_prompt, working_directory=str(cwd),
            timeout_seconds=timeout_seconds, writable=False, review_sha=gate.sha,
            gate_fingerprint=gate.fingerprint, allowed_environment=allowed_environment,
        )
        if not self.store.start_checker_invocation(
            lease, task_id, {"unit_id": identity.unit_id, "runtime": identity.runtime,
                             "model": identity.model}, sha=gate.sha,
            gate_fingerprint=gate.fingerprint, invocation_id=invocation_id, now=now,
        ):
            raise ValueError("checker invocation was already started")
        started_at = time.time()
        try:
            result = self._invoke_under_lease(lease, adapter, request, now=now)
        except LeaseError:
            raise
        except Exception:
            finished_at = time.time()
            failure = "checker runtime adapter failed"
            result = InvocationResult(
                invocation_id, task_id, "checker", identity.unit_id, identity.runtime,
                identity.model, "launch_failed", None, max(0.0, finished_at - started_at),
                "", failure, False, hashlib.sha256(failure.encode()).hexdigest(),
                started_at, finished_at, gate.sha, gate.fingerprint,
            )
        if (result.invocation_id != invocation_id or result.task_id != task_id
                or result.purpose != "checker" or result.unit_id != identity.unit_id
                or result.runtime != identity.runtime or result.model != identity.model
                or result.review_sha != gate.sha
                or result.gate_fingerprint != gate.fingerprint):
            raise ValueError("runtime adapter returned a mismatched checker receipt")
        if result.role is not None and result.role != identity.role:
            raise ValueError("runtime adapter returned a mismatched checker role")
        if result.role is None:
            result = replace(result, role=identity.role)
        self.store.record_invocation(lease, result, now=now)
        return TaskCheckerCycleResult(task_id, checker_id, result)

    def apply_checker_output(self, lease: HeadLease, cycle: TaskCheckerCycleResult, *,
                             gate: GateResult, event_id: str,
                             memory: EvidenceMemory | None = None,
                             project_root: str | Path | None = None,
                             expected_plan_sha256: str | None = None,
                             now: float | None = None) -> bool:
        """Normalize a successful checker response and apply it under HEAD authority."""
        invocation = cycle.invocation
        if (invocation.status != "succeeded" or invocation.exit_code != 0
                or invocation.task_id != cycle.task_id or invocation.purpose != "checker"):
            raise ValueError("review requires a successful checker invocation result")
        worker = self.registry.resolve(cycle.checker_id)
        identity = worker.identity
        if (invocation.unit_id != identity.unit_id or invocation.runtime != identity.runtime
                or invocation.model != identity.model or invocation.role != identity.role):
            raise ValueError("checker result does not match its configured worker identity")
        verdict = parse_review_verdict(invocation.stdout, identity)
        if (verdict.task_id != cycle.task_id or verdict.sha != gate.sha
                or verdict.gate_fingerprint != gate.fingerprint):
            raise ValueError("checker verdict does not match the task and registered gate")
        injection_event_id = None
        matching_fact_ids: tuple[str, ...] = ()
        if memory is not None:
            maker_invocations = [event.get("payload", event)
                                 for event in self.store.events()
                                 if isinstance(event.get("payload", event), Mapping)
                                 and event.get("payload", event).get("type") == "runtime.invoked"
                                 and event.get("payload", event).get("task_id") == cycle.task_id
                                 and event.get("payload", event).get("purpose") == "maker"
                                 and event.get("payload", event).get("status") == "succeeded"
                                 and event.get("payload", event).get("finished_at", math.inf)
                                 <= invocation.started_at]
            maker_invocations.sort(key=lambda item: (
                item.get("finished_at", 0), item.get("invocation_id", "")))
            if maker_invocations:
                injection_event_id = maker_invocations[-1].get("memory_injection_event_id")
            if injection_event_id is not None:
                if not isinstance(injection_event_id, str) or not injection_event_id.strip():
                    raise ValueError("maker invocation has an invalid memory event reference")
                injected = set(memory.fact_ids_for_injection(
                    injection_event_id, task_id=cycle.task_id))
                cited = {ref.removeprefix("memory-fact:")
                         for ref in verdict.evidence_refs if ref.startswith("memory-fact:")}
                if not cited.issubset(injected):
                    raise ValueError("review cites a memory fact outside this task's injection")
                matching_fact_ids = tuple(sorted(cited))
        applied = self.store.apply_review(
            lease, verdict, gate, checker_invocation_id=invocation.invocation_id,
            event_id=event_id, project_root=project_root,
            expected_plan_sha256=expected_plan_sha256, now=now,
        )
        if memory is not None and injection_event_id is not None:
            memory.record_review_outcome(
                event_id="memory-review:" + event_id,
                injection_event_id=injection_event_id,
                outcome="approved" if verdict.decision == "approved" else "rework",
                matching_fact_ids=(() if verdict.decision == "approved" else matching_fact_ids),
                now=now,
            )
        return applied

    def requeue_changes_requested_task(self, lease: HeadLease, task_id: str, *,
                                       event_id: str, max_rework_attempts: int = 1,
                                       now: float | None = None) -> bool:
        """Requeue only within a bounded number of checker-requested revisions.

        Once the task exceeds the configured cap, it remains open and in
        progress, a durable exhaustion event is recorded, and no new maker
        claim is created. Human recovery is then required.
        """
        if self.store.get_supervisor_mode()["mode"] != "running":
            raise ValueError("task requeue requires supervisor running mode")
        if (isinstance(max_rework_attempts, bool)
                or not isinstance(max_rework_attempts, int)
                or max_rework_attempts < 0):
            raise ValueError("max_rework_attempts must be a non-negative integer")
        task = self.store.get_task(task_id)
        if task is None or task.state is not TaskState.IN_PROGRESS or not task.assigned_worker:
            raise ValueError("only an in-progress assigned task can be requeued")
        review_events = [event for event in self.store.events()
                         if event["payload"].get("type") == "task.reviewed"
                         and event["payload"].get("task_id") == task_id]
        reviews = [event["payload"] for event in review_events]
        if (not reviews or reviews[-1].get("decision") != "changes_requested"
                or reviews[-1].get("maker_identity") != task.maker_identity):
            raise ValueError("requeue requires the latest task review to request changes")
        rework_count = sum(review.get("decision") == "changes_requested" for review in reviews)
        if rework_count > max_rework_attempts:
            latest_review_event_id = review_events[-1]["event_id"]
            self.store.record_event(
                lease, event_id,
                {"type": "supervisor.rework_limit_reached", "task_id": task_id,
                 "max_rework_attempts": max_rework_attempts,
                 "changes_requested_count": rework_count,
                 "latest_review_event_id": latest_review_event_id,
                 "task_state": task.state.value},
                now=now,
            )
            return False
        if not task.maker_identity or task.maker_identity.get("unit_id") != task.assigned_worker:
            raise ValueError("requeue requires the original maker identity snapshot")
        if any(other.task_id != task_id and other.assigned_worker == task.assigned_worker
               and other.state in {TaskState.CLAIMED, TaskState.IN_PROGRESS, TaskState.NEEDS_REVIEW}
               for other in self.store.list_tasks()):
            raise ValueError("maker already has another active task")
        return self.store.transition_task(
            lease, task_id, TaskState.CLAIMED, event_id=event_id,
            expected_state=TaskState.IN_PROGRESS, now=now,
        )

    def invoke_claimed_task(self, lease: HeadLease, task_id: str, *,
                            invocation_id: str, prompt: str, base_ref: str,
                            worktrees: GitWorktreeManager,
                            adapters: Mapping[str, InvocationAdapter],
                            project_root: str | Path | None = None,
                            timeout_seconds: float = 900.0,
                            state_db_path: str | Path | None = None,
                            worktree_copy: tuple[str, ...] = (),
                            allowed_environment: tuple[str, ...] = (),
                            memory_injection: FactInjection | None = None,
                            defer_control_drain: bool = False,
                            _ownership_retry_attempt: int = 0,
                            now: float | None = None) -> TaskInvocationCycleResult:
        """Launch one claimed maker once, in its isolated worktree, under HEAD lease.

        This is a bounded synchronous cycle: adapters own their timeout/reaping
        behavior. It does not parse model output into CONTROL or approve work.
        """
        if self.store.get_supervisor_mode()["mode"] != "running":
            raise ValueError("task invocation requires supervisor running mode")
        if not isinstance(defer_control_drain, bool):
            raise ValueError("defer_control_drain must be boolean")
        if _ownership_retry_attempt not in {0, 1}:
            raise ValueError("ownership retry attempt must be zero or one")
        if (not isinstance(prompt, str) or not prompt.strip()
                or not isinstance(base_ref, str) or not base_ref.strip()):
            raise ValueError("maker prompt and base_ref are required")
        if (not isinstance(invocation_id, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", invocation_id)
                or isinstance(timeout_seconds, bool)
                or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("invocation_id or timeout_seconds is invalid")
        task = self.store.get_task(task_id)
        if task is None:
            raise ValueError(f"unknown task: {task_id}")
        if task.state is not TaskState.CLAIMED or not task.assigned_worker:
            raise ValueError("only a claimed task can be launched")
        effective_prompt = prompt
        if memory_injection is not None:
            memory_context = render_fact_injection(memory_injection, task_id=task_id)
            if memory_context:
                effective_prompt += "\n\n" + memory_context
        worker = self.registry.resolve(task.assigned_worker)
        if task.assigned_worker not in self.registry.active:
            raise ValueError("assigned worker is no longer active")
        if self.policy.require_strict and worker.control_mode != "strict":
            raise ValueError("strict supervisor policy refuses an unverified worker")
        expected_identity = {"unit_id": worker.identity.unit_id,
                             "runtime": worker.identity.runtime,
                             "model": worker.identity.model}
        if task.maker_identity != expected_identity:
            raise ValueError("claimed task maker snapshot differs from the active registry")
        adapter = adapters.get(worker.identity.runtime)
        if adapter is None or not callable(getattr(adapter, "invoke", None)):
            raise ValueError(f"no invocation adapter for runtime {worker.identity.runtime}")

        worktree_info = worktrees.create(task.task_id,
                                         f"codexdevteam/{task.task_id}", base_ref,
                                         worktree_copy=worktree_copy)
        worktree = worktree_info.path
        status = subprocess.run(
            ["git", "-C", str(worktree), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True, text=True, check=False,
        )
        if _ownership_retry_attempt == 1:
            try:
                if status.returncode:
                    raise ValueError("retry worktree status could not be verified")
                validate_owned_retry_worktree(
                    worktrees.repository, worktree, task,
                    task_branch=f"codexdevteam/{task.task_id}",
                    expected_parent=worktree_info.head,
                    ignored_allowlist=self.policy.ignored_paths_allowlist,
                )
            except Exception as exc:
                self.store.transition_task(
                    lease, task_id, TaskState.BLOCKED,
                    event_id=f"host-commit-retry-preflight:{task_id}:{invocation_id}",
                    expected_state=TaskState.CLAIMED,
                    project_root=project_root, now=now,
                )
                self.store.record_event(
                    lease, f"host-commit-retry-preflight-detail:{task_id}:{invocation_id}",
                    {"type": "task.blocked_before_host_commit_retry", "task_id": task_id,
                     "invocation_id": invocation_id,
                     "reason": f"OWNERSHIP_CONFLICT: retry worktree validation failed: {exc}"},
                    now=now,
                )
                raise ValueError("bounded retry blocked because worktree validation failed") from exc
        elif status.returncode or status.stdout.strip():
            raise ValueError("maker worktree must be clean and verifiable before launch")

        database = Path(state_db_path) if state_db_path is not None else self.store.path
        if database.is_symlink():
            raise ValueError("maker state database cannot be a symlink")
        database = database.resolve(strict=True)
        if not database.is_file():
            raise ValueError("maker state database must be a regular file")
        git_dir_result = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "--absolute-git-dir"],
            capture_output=True, text=True, check=False,
        )
        if git_dir_result.returncode:
            raise ValueError("maker worktree Git metadata is unavailable")
        git_dir_input = Path(git_dir_result.stdout.strip())
        if git_dir_input.is_symlink():
            raise ValueError("maker worktree Git metadata directory is unsafe")
        git_dir = git_dir_input.resolve(strict=True)
        if not git_dir.is_dir():
            raise ValueError("maker worktree Git metadata directory is unsafe")
        invocation_key = hashlib.sha256(invocation_id.encode("utf-8")).hexdigest()[:24]
        snapshot = self.store.create_task_snapshot(
            git_dir / f"codexdevteam-state-{invocation_key}.sqlite",
            task_id, source_path=database,
        )
        started_at = time.time()
        cancellation_token = secrets.token_urlsafe(48)
        try:
            request = request_for_worker(
                self.registry, worker.identity.unit_id,
                invocation_id=invocation_id, task_id=task_id, purpose="maker",
                prompt=effective_prompt, working_directory=str(worktree),
                timeout_seconds=timeout_seconds, writable=True,
                allowed_environment=allowed_environment,
                state_db_path=str(snapshot),
                control_outbox_path=str(_control_outbox_path(worktree, invocation_id)),
                cancellation_receipt_dir=str(self.store.cancellation_receipt_directory),
                cancellation_token=cancellation_token,
                on_process_start=lambda identity: self.store.record_task_invocation_process(
                    lease, task_id, invocation_id, identity, now=now,
                ),
            )
            if not self.store.start_task_invocation(
                    lease, task_id, invocation_id, project_root=project_root,
                    cancellation_token=cancellation_token, now=now):
                raise ValueError("maker invocation was already started")
            try:
                result = self._invoke_under_lease(
                    lease, adapter, request, now=now, heartbeat_task_id=task_id,
                )
            except LeaseError:
                raise
            except Exception as exc:
                finished_at = time.time()
                failure_type = type(exc).__name__
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", failure_type):
                    failure_type = "Exception"
                result = InvocationResult(
                    invocation_id, task_id, "maker", worker.identity.unit_id,
                    worker.identity.runtime, worker.identity.model, "launch_failed", None,
                    max(0.0, finished_at - started_at), "",
                    f"runtime adapter raised {failure_type}",
                    False, hashlib.sha256(b"runtime adapter failed").hexdigest(),
                    started_at, finished_at,
                    process_tree_cancel_method="runtime_adapter_failure",
                    process_tree_cancel_verified=False,
                )
        finally:
            _cleanup_task_snapshot(snapshot)
        if (result.invocation_id != invocation_id or result.task_id != task_id
                or result.purpose != "maker" or result.unit_id != worker.identity.unit_id
                or result.runtime != worker.identity.runtime
                or result.model != worker.identity.model):
            raise ValueError("runtime adapter returned a mismatched invocation receipt")
        if result.role is not None and result.role != worker.identity.role:
            raise ValueError("runtime adapter returned a mismatched maker role")
        if result.role is None:
            result = replace(result, role=worker.identity.role)
        if memory_injection is not None and memory_injection.facts:
            result = replace(result, memory_injection_event_id=memory_injection.event_id)
        commit_result = None
        if result.status == "succeeded":
            proof = result.quiescence_proof
            if (os.name == "nt" and isinstance(proof, QuiescenceProof)
                    and proof.platform == "windows"
                    and proof.mechanism == "windows_job_object" and proof.verified):
                commit_result = host_commit(
                    worktrees.repository, worktree, task,
                    task_branch=f"codexdevteam/{task.task_id}",
                    expected_parent=worktree_info.head,
                    invocation_id=invocation_id,
                    quiescence=proof,
                    limits=CommitLimits(
                        ignored_allowlist=(".codexdevteam/control",
                                           *self.policy.ignored_paths_allowlist)),
                )
            else:
                commit_result = HostCommitResult(
                    "refused",
                    reasons=(RefusalReason(
                        "QUIESCENCE_UNPROVEN",
                        "successful maker has no verified Windows Job Object proof; no commit published",
                    ),),
                )
        self.store.record_invocation(lease, result, now=now)
        quarantine_path = None
        control_archive_path = None
        refused_control_names: tuple[str, ...] = ()
        archive_error = None
        retryable_ownership_refusal = False
        if commit_result is not None and commit_result.status == "refused":
            proof = result.quiescence_proof
            proof_is_quiescent = (isinstance(proof, QuiescenceProof) and proof.verified
                                  and proof.active_after_exit == 0
                                  and proof.platform == "windows"
                                  and proof.mechanism == "windows_job_object")
            if proof_is_quiescent:
                try:
                    control_archive_path, refused_control_names = archive_refused_control_outbox(
                        worktrees.repository, worktree, task,
                        task_branch=f"codexdevteam/{task.task_id}",
                        invocation_id=invocation_id,
                        outbox=_control_outbox_path(worktree, invocation_id),
                    )
                except Exception as exc:
                    archive_error = f"{type(exc).__name__}: {exc}"
            reason_codes = [reason.code for reason in commit_result.reasons]
            if (proof_is_quiescent and "OUTSIDE_TERRITORY" in reason_codes
                    and commit_result.paths):
                try:
                    quarantine_path, _ = quarantine_out_of_scope_changes(
                        worktrees.repository, worktree, task,
                        task_branch=f"codexdevteam/{task.task_id}",
                        expected_parent=worktree_info.head, invocation_id=invocation_id,
                        paths=commit_result.paths,
                        ignored_allowlist=self.policy.ignored_paths_allowlist,
                    )
                    retryable_ownership_refusal = True
                except Exception as exc:
                    archive_error = (archive_error + "; " if archive_error else "") + (
                        f"quarantine failed: {type(exc).__name__}: {exc}")
            retry_queued = (retryable_ownership_refusal and archive_error is None
                            and _ownership_retry_attempt == 0)
            refusal_digest = hashlib.sha256(json.dumps({
                "task_id": task_id, "invocation_id": invocation_id,
                "reason_codes": reason_codes, "paths": list(commit_result.paths),
                "reasons": [{"code": reason.code, "detail": reason.detail}
                            for reason in commit_result.reasons],
            }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            self.store.record_event(
                lease, f"host-commit:{invocation_id}",
                {
                    "type": "host_commit.refused",
                    "task_id": task_id,
                    "invocation_id": invocation_id,
                    "status": commit_result.status,
                    "sha": None,
                    "paths": list(commit_result.paths),
                    "reason_codes": reason_codes,
                    "reasons": [{"code": reason.code, "detail": reason.detail}
                                for reason in commit_result.reasons],
                    "control_reports_archived": list(refused_control_names),
                    "control_archive_path": (str(control_archive_path)
                                             if control_archive_path else None),
                    "control_archive_error": archive_error,
                    "quarantine_path": str(quarantine_path) if quarantine_path else None,
                    "retry_queued": retry_queued,
                    "refusal_digest": refusal_digest,
                }, now=now,
            )
            current_task = self.store.get_task(task_id)
            if current_task is not None and current_task.state is TaskState.IN_PROGRESS:
                if retry_queued:
                    self.store.transition_task(
                        lease, task_id, TaskState.CLAIMED,
                        event_id=f"host-commit-retry:{task_id}:{invocation_id}",
                        expected_state=TaskState.IN_PROGRESS,
                        project_root=project_root, now=now,
                    )
                else:
                    code = ("OWNERSHIP_CONFLICT" if "OUTSIDE_TERRITORY" in reason_codes
                            else "HOST_COMMIT_REFUSED")
                    detail = ", ".join(commit_result.paths) or "; ".join(
                        reason.detail for reason in commit_result.reasons)
                    blocked_reason = f"{code}: host commit refused: {detail}"
                    self.store.transition_task(
                        lease, task_id, TaskState.BLOCKED,
                        event_id=f"host-commit-blocked:{task_id}:{invocation_id}",
                        expected_state=TaskState.IN_PROGRESS,
                        project_root=project_root, now=now,
                    )
                    self.store.record_event(
                        lease, f"host-commit-blocked-detail:{task_id}:{invocation_id}",
                        {"type": "task.blocked_by_host_commit", "task_id": task_id,
                         "invocation_id": invocation_id, "reason": blocked_reason,
                         "paths": list(commit_result.paths),
                         "refusal_digest": refusal_digest}, now=now,
                    )
            if retry_queued:
                retry_id = "retry-" + hashlib.sha256(
                    f"{task_id}:{invocation_id}".encode("utf-8")).hexdigest()[:32]
                retry_detail = "; ".join(
                    reason.detail for reason in commit_result.reasons)
                retry_paths = ", ".join(commit_result.paths)
                retry_prompt = (prompt + "\n\nHEAD COMMIT REFUSAL — ONE BOUNDED RETRY\n"
                                f"The host refused the previous commit: {retry_detail}.\n"
                                f"Out-of-scope paths quarantined and removed: {retry_paths}.\n"
                                "Continue only within the task's Owned_Paths. The prior owned-path "
                                "changes remain in this worktree. Do not recreate the refused paths.")
                return self.invoke_claimed_task(
                    lease, task_id, invocation_id=retry_id, prompt=retry_prompt,
                    base_ref=base_ref, worktrees=worktrees, adapters=adapters,
                    project_root=project_root, timeout_seconds=timeout_seconds,
                    state_db_path=state_db_path, worktree_copy=worktree_copy,
                    allowed_environment=allowed_environment,
                    memory_injection=memory_injection,
                    defer_control_drain=defer_control_drain,
                    _ownership_retry_attempt=1, now=now,
                )
        elif commit_result is not None:
            self.store.record_event(
                lease, f"host-commit:{invocation_id}",
                {"type": "host_commit.completed", "task_id": task_id,
                 "invocation_id": invocation_id, "status": commit_result.status,
                 "sha": commit_result.sha, "paths": list(commit_result.paths),
                 "reason_codes": []}, now=now,
            )
        elif _ownership_retry_attempt == 1 and result.status != "succeeded":
            current_task = self.store.get_task(task_id)
            if current_task is not None and current_task.state is TaskState.IN_PROGRESS:
                self.store.transition_task(
                    lease, task_id, TaskState.BLOCKED,
                    event_id=f"host-commit-retry-failed:{task_id}:{invocation_id}",
                    expected_state=TaskState.IN_PROGRESS,
                    project_root=project_root, now=now,
                )
                self.store.record_event(
                    lease, f"host-commit-retry-failed-detail:{task_id}:{invocation_id}",
                    {"type": "task.blocked_after_host_commit_retry", "task_id": task_id,
                     "invocation_id": invocation_id,
                     "reason": f"OWNERSHIP_CONFLICT: bounded retry ended with {result.status}"},
                    now=now,
                )
        control = ({"applied": (), "rejected": ()}
                   if defer_control_drain or (
                       commit_result is not None and commit_result.status == "refused") else
                   drain_control_outbox(
                       self.store, lease,
                       _control_outbox_path(worktree, invocation_id),
                       expected_invocation_id=invocation_id, now=now,
                   ))
        return TaskInvocationCycleResult(task_id, worker.identity.unit_id,
                                         str(worktree), result,
                                         control["applied"], control["rejected"],
                                         commit_result)

    def _invoke_under_lease(self, lease: HeadLease, adapter: InvocationAdapter,
                            request: InvocationRequest, *, now: float | None,
                            heartbeat_task_id: str | None = None,
                            ) -> InvocationResult:
        """Keep the cross-process HEAD lease alive during a real runtime call."""
        if now is not None:
            return adapter.invoke(request)
        on_renew = (None if heartbeat_task_id is None else
                    lambda current_lease: self.store.heartbeat_task_invocation(
                        current_lease, heartbeat_task_id, request.invocation_id))
        guard = HeadLeaseGuard(self.store, lease, on_renew=on_renew)
        with guard:
            result = adapter.invoke(replace(request, cancel_event=guard.lost_event))
        guard.raise_if_lost()
        return result


def _control_outbox_path(worktree: str | Path, invocation_id: str) -> Path:
    key = hashlib.sha256(invocation_id.encode("utf-8")).hexdigest()
    return Path(worktree) / ".codexdevteam" / "control" / "outbox" / key


def _cleanup_task_snapshot(snapshot: Path) -> None:
    """Remove only this invocation's SQLite snapshot and known sidecars."""
    for suffix in ("", "-journal", "-wal", "-shm"):
        path = snapshot if not suffix else Path(str(snapshot) + suffix)
        if path.is_symlink():
            path.unlink()
            continue
        if not path.exists():
            continue
        if not path.is_file():
            raise ValueError(f"refusing to remove non-file task snapshot artifact: {path.name}")
        if not suffix and os.name == "nt":
            path.chmod(0o600)
        path.unlink()
