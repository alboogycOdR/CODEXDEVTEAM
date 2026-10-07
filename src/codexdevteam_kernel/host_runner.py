"""Composable bounded host-cycle inputs and mandatory maker closeout."""

import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from threading import Event

from .gate import GateRunner
from .host_config import WindowsHostConfig
from .host_runtime import HostRuntimeBindings, load_host_runtime
from .onboarding import OnboardingMode, inspect_project
from .plan_markdown import parse_plan_markdown
from .prompts import render_checker_prompt, render_maker_prompt
from .state import HeadLease, StateStore, TaskRecord
from .supervisor import (MakerCloseoutResult, Supervisor,
                         ContinuousSupervisorResult,
                         SupervisorLaunchCycleResult, SupervisorPolicy)
from .tasks import TaskState
from .worktrees import GitWorktreeManager


def request_host_stop(config: WindowsHostConfig, *, reason: str) -> str:
    """Create one atomic, host-owned stop request for a running Windows host."""
    if not isinstance(config, WindowsHostConfig):
        raise ValueError("stop request requires WindowsHostConfig")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("stop request requires an operator-visible reason")
    root = config.control_root
    metadata_root = config.project_root / ".codexdevteam"
    if metadata_root.is_symlink() or root.is_symlink():
        raise ValueError("host control path cannot traverse a symlink")
    if root.exists() and not root.is_dir():
        raise ValueError("host control path must be a directory")
    root.mkdir(parents=True, exist_ok=True)
    request_id = uuid.uuid4().hex
    path = root / "STOP.json"
    if path.is_symlink():
        raise ValueError("host stop request cannot be a symlink")
    payload = {
        "protocol_version": 1,
        "request_id": request_id,
        "reason": reason.strip(),
        "requested_at": time.time(),
    }
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
    except FileExistsError as exc:
        raise ValueError("a host stop request is already pending") from exc
    return request_id


def watch_host_stop_request(config: WindowsHostConfig, store: StateStore,
                            lease: HeadLease, stop_event: Event,
                            watcher_stop: Event, *, interval_seconds: float = 0.5) -> None:
    """Poll the host-owned stop request and signal the active loop once."""
    path = config.control_root / "STOP.json"
    while not watcher_stop.wait(interval_seconds):
        if ((config.project_root / ".codexdevteam").is_symlink()
                or config.control_root.is_symlink()):
            try:
                park_configured_host(
                    store, lease, stop_event,
                    reason="host control path changed to a symlink; operator review required",
                )
            finally:
                return
        if not path.exists():
            continue
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError("stop request is not a regular file")
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(payload, dict)
                    or set(payload) != {"protocol_version", "request_id", "reason", "requested_at"}
                    or payload.get("protocol_version") != 1
                    or not isinstance(payload.get("request_id"), str)
                    or not re.fullmatch(r"[0-9a-f]{32}", payload["request_id"])
                    or not isinstance(payload.get("reason"), str)
                    or not payload["reason"].strip()):
                raise ValueError("stop request schema is invalid")
            park_configured_host(store, lease, stop_event, reason=payload["reason"])
            acknowledged = config.control_root / ("STOP." + payload["request_id"] + ".ack.json")
            if acknowledged.exists() or acknowledged.is_symlink():
                raise ValueError("stop acknowledgement path already exists")
            os.rename(path, acknowledged)
            return
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            try:
                park_configured_host(
                    store, lease, stop_event,
                    reason="invalid host stop request; operator review required",
                )
                failed = config.control_root / ("STOP.invalid." + uuid.uuid4().hex + ".json")
                if path.exists() and not path.is_symlink():
                    os.rename(path, failed)
            except Exception:
                stop_event.set()
            return


def activate_fresh_host(config: WindowsHostConfig, store: StateStore, *, confirmed: bool,
                        takeover_confirmed: bool = False) -> HeadLease:
    """Explicitly activate only an installed fresh project and acquire its HEAD lease.

    Existing DEVDEPARTMENT sidecars are categorically refused here; their
    activation belongs to the separately evidenced handover transaction.
    """
    if os.name != "nt":
        raise RuntimeError("host activation is supported on Windows only")
    if not isinstance(config, WindowsHostConfig):
        raise ValueError("activation requires validated host configuration")
    if not isinstance(store, StateStore) or store.path.resolve() != config.state_db.resolve():
        raise ValueError("activation requires the configured StateStore")
    if confirmed is not True:
        raise ValueError("fresh-project activation requires explicit operator confirmation")
    if config.instance_id == "configure-host-instance":
        raise ValueError("configure a unique host instance_id before activation")
    control_root = config.control_root
    if ((config.project_root / ".codexdevteam").is_symlink()
            or control_root.is_symlink()):
        raise ValueError("host control path cannot traverse a symlink")
    pending_stop = control_root / "STOP.json"
    if pending_stop.is_symlink() or pending_stop.exists():
        raise ValueError("a pending host stop request must be inspected before activation")
    bindings = load_host_runtime(config)
    inspection = inspect_project(config.project_root)
    marker_path = config.project_root / ".codexdevteam" / "installation.json"
    if (inspection.mode is not OnboardingMode.CODEXDEVTEAM_UPGRADE
            or inspection.devdepartment_present or marker_path.is_symlink()
            or not marker_path.is_file()):
        raise ValueError("activation requires an installed fresh CODEXDEVTEAM project without DEVDEPARTMENT")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("CODEXDEVTEAM installation marker is invalid") from exc
    if (not isinstance(marker, dict) or marker.get("integration_mode") != "fresh"
            or marker.get("activated") is not False or marker.get("active_head") is not None):
        raise ValueError("project marker is not an inactive fresh-project installation")
    if store.get_supervisor_mode().get("mode") != "parked":
        raise ValueError("supervisor must be parked before activation")
    observations = config.load_capacity_observations()
    active_ids = set(bindings.registry.active)
    if not active_ids or not active_ids.issubset(observations):
        raise ValueError("activation requires capacity observations for every active worker")
    now = time.time()
    ineligible = sorted(worker_id for worker_id in active_ids
                        if not observations[worker_id].eligible(now=now))
    if ineligible:
        raise ValueError("activation refused; active workers are unavailable or stale: "
                         + ", ".join(ineligible))
    lease = store.acquire_head(
        config.system_id, config.instance_id,
        ttl_seconds=config.lease_ttl_seconds,
        takeover_confirmed=takeover_confirmed,
    )
    try:
        store.set_supervisor_mode(
            lease, "running", event_id="host-activate-" + uuid.uuid4().hex,
            reason="operator-confirmed fresh-project activation",
        )
        return lease
    except Exception:
        try:
            store.release_head(lease)
        except Exception:
            pass
        raise


def build_host_cycle_inputs(config: WindowsHostConfig, bindings: HostRuntimeBindings,
                            store: StateStore, worktrees: GitWorktreeManager
                            ) -> dict[str, object]:
    """Build fresh, authoritative inputs for one dispatch tick without dispatching."""
    if not isinstance(config, WindowsHostConfig):
        raise ValueError("cycle input construction requires WindowsHostConfig")
    if not isinstance(bindings, HostRuntimeBindings) or not isinstance(store, StateStore):
        raise ValueError("cycle inputs require validated bindings and StateStore")
    if not isinstance(worktrees, GitWorktreeManager):
        raise ValueError("cycle inputs require a GitWorktreeManager")
    if worktrees.repository != config.project_root:
        raise ValueError("worktree manager repository differs from configured project root")
    head = subprocess.run(["git", "-C", str(config.project_root), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=False)
    status = subprocess.run(
        ["git", "-C", str(config.project_root), "status", "--porcelain",
         "--untracked-files=all", "--", ".", ":(exclude).codexdevteam/**",
         ":(exclude)PLAN.md"],
        capture_output=True, text=True, check=False,
    )
    if head.returncode or not head.stdout.strip():
        raise ValueError("configured project has no verifiable Git HEAD")
    if status.returncode or status.stdout.strip():
        raise ValueError("configured project must be clean outside host-projected PLAN state")
    _verify_plan_matches_authoritative_state(config.project_root, store)

    pending = [task for task in store.list_tasks() if task.state.value == "pending"]
    prompts = {task.task_id: render_maker_prompt(task) for task in pending}
    invocation_ids = {task.task_id: "maker-" + uuid.uuid4().hex for task in pending}
    capacity = config.load_capacity_observations()
    active = set(bindings.registry.active)
    missing = sorted(active - set(capacity))
    unknown = sorted(set(capacity) - set(bindings.registry.defined))
    if missing or unknown:
        details = []
        if missing:
            details.append("missing active workers " + ", ".join(missing))
        if unknown:
            details.append("undefined workers " + ", ".join(unknown))
        raise ValueError("invalid capacity snapshot: " + "; ".join(details))

    return {
        "capacity": capacity,
        "task_prompts": prompts,
        "invocation_ids": invocation_ids,
        "base_ref": head.stdout.strip(),
        "worktrees": worktrees,
        "adapters": bindings.adapters,
        "max_tasks": 1,
        "project_root": config.project_root,
        "state_db_path": config.state_db,
        "allowed_environment": bindings.environment_allowlist,
    }


def _verify_plan_matches_authoritative_state(project_root: Path, store: StateStore) -> None:
    """Allow only lease-projected task state changes in PLAN between cycles."""
    path = project_root / "PLAN.md"
    if path.is_symlink() or not path.is_file():
        raise ValueError("unattended dispatch requires a regular PLAN.md")
    try:
        parsed = parse_plan_markdown(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read authoritative PLAN.md: {exc}") from exc
    if parsed.findings:
        raise ValueError("PLAN.md parser findings block dispatch: " + "; ".join(parsed.findings))
    state_tasks = {task.task_id: task for task in store.list_tasks()}
    plan_tasks = {task.task_id: task for task in parsed.tasks}
    if set(plan_tasks) != set(state_tasks):
        raise ValueError("PLAN.md task IDs differ from the authoritative task store")

    # The state record accumulates runtime test-run evidence after seeding, while
    # PLAN.md retains the task's original planned test evidence. Compare both
    # against the immutable seed snapshot so a legitimate gate receipt does not
    # look like PLAN tampering on the next dispatch cycle.
    seeded_tasks: dict[str, TaskRecord] = {}
    for event in store.events():
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "task.seeded":
            continue
        raw_task = payload.get("task")
        if not isinstance(raw_task, dict):
            continue
        seeded = TaskRecord.from_dict(raw_task)
        seeded_tasks.setdefault(seeded.task_id, seeded)

    mutable_fields = {"state", "assigned_worker", "maker_identity"}
    state_runtime_fields = mutable_fields | {"test_evidence"}
    for task_id, authoritative in state_tasks.items():
        projected = plan_tasks[task_id]
        seeded = seeded_tasks.get(task_id, authoritative)
        baseline = seeded.to_dict()
        state_content = {key: value for key, value in authoritative.to_dict().items()
                         if key not in state_runtime_fields}
        seeded_content = {key: value for key, value in baseline.items()
                          if key not in state_runtime_fields}
        plan_content = {key: value for key, value in projected.to_dict().items()
                        if key not in mutable_fields}
        planned_content = {key: value for key, value in baseline.items()
                           if key not in mutable_fields}
        if state_content != seeded_content:
            raise ValueError(f"authoritative task content changed after seeding for {task_id}")
        if plan_content != planned_content:
            raise ValueError(f"PLAN.md changed authoritative task content for {task_id}")
        if (projected.state is not authoritative.state
                or projected.assigned_worker != authoritative.assigned_worker):
            raise ValueError(f"PLAN.md projected state differs from authoritative store for {task_id}")
    expected_archives = set(store.historical_task_ids()) | {
        task.task_id for task in store.archived_tasks()
    }
    if set(parsed.archived_task_ids) != expected_archives:
        raise ValueError("PLAN.md archived task IDs differ from authoritative archive state")


def closeout_host_cycle(supervisor: Supervisor, lease: HeadLease,
                        result: SupervisorLaunchCycleResult,
                        config: WindowsHostConfig, bindings: HostRuntimeBindings,
                        gate_runner: GateRunner, *, base_ref: str
                        ) -> tuple[MakerCloseoutResult, ...]:
    """Gate and independently review every maker before a loop can continue."""
    if not isinstance(supervisor, Supervisor) or not isinstance(lease, HeadLease):
        raise ValueError("host closeout requires a Supervisor and current HEAD lease")
    if not isinstance(result, SupervisorLaunchCycleResult):
        raise ValueError("host closeout requires a completed launch cycle")
    if not isinstance(base_ref, str) or not base_ref.strip():
        raise ValueError("host closeout requires the dispatch cycle base ref")
    checker_candidates = [
        worker for worker in bindings.registry.active_workers()
        if worker.identity.role == config.checker_role
        or (config.checker_role == "reviewer" and worker.identity.role == "judgment")
    ]
    if not checker_candidates:
        raise ValueError("no active checker worker matches configured checker_role")
    closeouts: list[MakerCloseoutResult] = []
    worktrees = GitWorktreeManager(config.project_root, config.worktree_root)
    for maker in result.makers:
        current_maker = maker
        for rework_number in range(config.max_rework_attempts + 1):
            task = supervisor.store.get_task(current_maker.task_id)
            if task is None:
                raise ValueError(f"maker task disappeared before closeout: {current_maker.task_id}")
            checker = next((worker for worker in checker_candidates
                            if task.maker_identity is not None
                            and worker.identity.unit_id != task.maker_identity["unit_id"]
                            and (worker.identity.runtime, worker.identity.model)
                            != (task.maker_identity["runtime"], task.maker_identity["model"])), None)
            if checker is None:
                raise ValueError(f"no independent checker is eligible for {current_maker.task_id}")
            closeout = supervisor.closeout_maker_with_checker(
                lease, current_maker, gate_runner=gate_runner,
                base_ref=base_ref,
                commands=bindings.gate_commands,
                checker_id=checker.identity.unit_id,
                checker_prompt=lambda checked_task, gate: render_checker_prompt(
                    checked_task, sha=gate.sha, gate_fingerprint=gate.fingerprint),
                adapters=bindings.adapters,
                gate_attempt_event_id="gate-attempt-" + uuid.uuid4().hex,
                checker_invocation_id="checker-" + uuid.uuid4().hex,
                review_event_id="review-" + uuid.uuid4().hex,
                allowed_environment=bindings.environment_allowlist,
                project_root=config.project_root,
            )
            closeouts.append(closeout)
            if closeout.review_applied:
                integrated_task = supervisor.store.get_task(current_maker.task_id)
                if integrated_task is not None and integrated_task.state is TaskState.DONE:
                    from .host_integration import integrate_approved_task

                    integrate_approved_task(
                        config, supervisor.store, lease, current_maker.task_id,
                        gate_runner, bindings.gate_commands,
                        allowed_environment=bindings.environment_allowlist,
                        expected_base_sha=base_ref,
                    )
            if closeout.checker is None:
                break
            checker_invocation_id = closeout.checker.invocation.invocation_id
            review = next((event for event in reversed(
                supervisor.store.verified_review_events())
                if event["payload"].get("task_id") == current_maker.task_id
                and event["payload"].get("checker_invocation_id") == checker_invocation_id), None)
            if review is None or review["payload"].get("decision") != "changes_requested":
                break
            if not supervisor.requeue_changes_requested_task(
                    lease, current_maker.task_id,
                    event_id="host-rework-queue-" + uuid.uuid4().hex,
                    max_rework_attempts=config.max_rework_attempts):
                break
            feedback = review["payload"]
            rework_task = supervisor.store.get_task(current_maker.task_id)
            if rework_task is None:
                raise ValueError(f"maker task disappeared before rework: {current_maker.task_id}")
            rework_prompt = (
                render_maker_prompt(rework_task)
                + "\n\nVERIFIED CHECKER REWORK REQUEST\n"
                + "The independent checker requested changes on the previous attempt.\n"
                + "Resume on the existing task branch. Inspect its current HEAD, changes, and relevant files first; preserve valid work.\n"
                + "Address the rationale and evidence below within the task's ownership.\n"
                + "Rationale: " + feedback["rationale"] + "\n"
                + "Evidence references: "
                + json.dumps(feedback["evidence_refs"], ensure_ascii=False) + "\n"
            )
            current_maker = supervisor.invoke_claimed_task(
                lease, current_maker.task_id,
                invocation_id="maker-rework-" + uuid.uuid4().hex,
                prompt=rework_prompt, base_ref=base_ref, worktrees=worktrees,
                adapters=bindings.adapters, project_root=config.project_root,
                state_db_path=config.state_db,
                allowed_environment=bindings.environment_allowlist,
                defer_control_drain=True,
            )
    return tuple(closeouts)


def recover_interrupted_host_tasks(config: WindowsHostConfig, store: StateStore,
                                   lease: HeadLease) -> tuple[str, ...]:
    """Contain orphaned makers and durably escalate tasks interrupted by restart.

    Recovery is deliberately conservative. A successor may prove that a maker
    process tree is gone using the persisted Windows Job Object identity, but
    that does not prove whether its task branch is ready for replay or review.
    Therefore every nonterminal claimed/review task is escalated and the caller
    must park before dispatch. Human review can then resume it deliberately.
    """
    if os.name != "nt":
        raise RuntimeError("standalone host restart recovery is supported on Windows only")
    if not isinstance(config, WindowsHostConfig) or not isinstance(store, StateStore):
        raise ValueError("restart recovery requires validated Windows host configuration and state")
    if store.path.resolve() != config.state_db.resolve():
        raise ValueError("restart recovery StateStore differs from configured state database")
    if not isinstance(lease, HeadLease):
        raise ValueError("restart recovery requires the current HEAD lease")

    # A signed cancellation receipt can complete liveness after the old HEAD
    # lost its lease. Verify it before inspecting or reaping remaining records.
    store.recover_invocation_cancellation_receipts(lease)
    running = store.task_invocation_liveness(state="running")
    for invocation in running:
        try:
            # Acquiring the successor lease establishes that any prior lease
            # expired or was explicitly taken over. Reap by persisted process
            # identity; only a whole-tree proof clears liveness.
            store.reap_stale_invocation(
                lease, invocation["invocation_id"], stale_after_seconds=0,
                grace_seconds=0.25, timeout_seconds=5.0,
            )
        except Exception:
            # Keep liveness held on every unverifiable result and create a
            # durable, operator-visible escalation below.
            pass

    active_tasks = {
        task.task_id: task for task in store.list_tasks()
        if task.state in {TaskState.CLAIMED, TaskState.IN_PROGRESS, TaskState.NEEDS_REVIEW}
    }
    remaining_running = store.task_invocation_liveness(state="running")
    for row in remaining_running:
        active_tasks.setdefault(row["task_id"], store.get_task(row["task_id"]))
    unresolved = tuple(sorted(
        set(active_tasks) | {row["task_id"] for row in remaining_running}
    ))
    for task_id in unresolved:
        task = store.get_task(task_id)
        state = task.state.value if task is not None else "unknown"
        live_rows = [row for row in store.task_invocation_liveness(task_id=task_id)
                     if row["state"] == "running"]
        process_note = (" A maker process tree is still unverified; dispatch remains held."
                        if live_rows else "")
        store.record_escalation(
            lease,
            escalation_key=f"{task_id}:host-restart-recovery",
            task_id=task_id,
            severity="high",
            message=(f"Host restarted while task was {state}. Inspect the preserved task "
                     f"branch and verified receipts before resuming.{process_note}"),
            event_id=f"host-restart-recovery:{lease.generation}:{task_id}",
        )
    store.record_event(
        lease, f"host-restart-recovery-scan:{lease.generation}",
        {"type": "supervisor.restart_recovery_completed",
         "interrupted_task_ids": list(unresolved),
         "running_invocation_ids": [row["invocation_id"] for row in remaining_running],
         "dispatch_permitted": not unresolved and not remaining_running},
    )
    return unresolved


def run_configured_host_loop(config: WindowsHostConfig,
                             store: StateStore,
                             lease: HeadLease,
                             stop_event: Event):
    """Run the bounded Windows supervisor loop under an already active lease.

    Activation, incumbent handover fencing, and lease acquisition belong to a
    separate host lifecycle transaction. This function refuses to run unless
    that transaction has already placed the durable supervisor mode in running.
    """
    if os.name != "nt":
        raise RuntimeError("supervised host execution is supported on Windows only")
    if not isinstance(config, WindowsHostConfig):
        raise ValueError("configured host loop requires validated configuration")
    if not isinstance(store, StateStore) or not isinstance(lease, HeadLease):
        raise ValueError("configured host loop requires StateStore and HEAD lease")
    if not isinstance(stop_event, Event):
        raise ValueError("stop_event must be a threading.Event")
    if store.path.resolve() != config.state_db.resolve():
        raise ValueError("StateStore path differs from configured state database")
    result = None
    active_lease = lease
    try:
        if (active_lease.system_id != config.system_id
                or active_lease.instance_id != config.instance_id):
            raise ValueError("HEAD lease identity differs from configured host identity")
        bindings = load_host_runtime(config)
        if store.get_supervisor_mode().get("mode") != "running":
            raise ValueError("host loop requires explicit prior activation in running mode")
        active_lease = store.renew_head(active_lease, ttl_seconds=config.lease_ttl_seconds)
        worktrees = GitWorktreeManager(config.project_root, config.worktree_root)
        supervisor = Supervisor(
            store, bindings.registry,
            SupervisorPolicy(role=config.maker_role,
                             require_strict=config.require_strict,
                             require_capacity_observation=config.require_capacity_observation,
                             task_class_policy=bindings.task_class_policy,
                             ignored_paths_allowlist=bindings.ignored_paths_allowlist),
        )
        gate_runner = GateRunner(
            config.project_root, config.project_root / ".codexdevteam" / "gates",
            protected_paths=bindings.protected_paths,
        )
        from .host_integration import (find_approved_tasks_awaiting_integration,
                                       recover_pending_integrations)

        integration_recovery_attention = recover_pending_integrations(
            config, store, active_lease)
        if integration_recovery_attention:
            reason = "integration_recovery_escalation_required"
            store.set_supervisor_mode(
                active_lease, "parked",
                event_id="host-integration-recovery-park-" + uuid.uuid4().hex,
                reason=("approved branches need integration recovery: "
                        + ", ".join(integration_recovery_attention)),
            )
            stop_event.set()
            result = ContinuousSupervisorResult(0, reason)
            return result
        unintegrated = find_approved_tasks_awaiting_integration(store, active_lease)
        if unintegrated:
            reason = "approved_tasks_awaiting_integration"
            store.set_supervisor_mode(
                active_lease, "parked",
                event_id="host-unintegrated-approved-park-" + uuid.uuid4().hex,
                reason="approved tasks lack integration receipts: " + ", ".join(unintegrated),
            )
            stop_event.set()
            result = ContinuousSupervisorResult(0, reason)
            return result
        interrupted = recover_interrupted_host_tasks(config, store, active_lease)
        if interrupted:
            reason = "restart_recovery_escalation_required"
            store.set_supervisor_mode(
                active_lease, "parked",
                event_id="host-restart-recovery-park-" + uuid.uuid4().hex,
                reason=("restart recovery escalated interrupted tasks: "
                        + ", ".join(interrupted)),
            )
            stop_event.set()
            result = ContinuousSupervisorResult(0, reason)
            return result
        base_ref_by_cycle: dict[str, str] = {}

        def cycle_inputs(tick: int) -> dict[str, object]:
            inputs = build_host_cycle_inputs(config, bindings, store, worktrees)
            base_ref_by_cycle[str(tick)] = str(inputs["base_ref"])
            return inputs

        def on_cycle(result: SupervisorLaunchCycleResult) -> None:
            tick = int(result.dispatch.cycle_id.rsplit(":", 1)[-1])
            closeout_host_cycle(
                supervisor, active_lease, result, config, bindings, gate_runner,
                base_ref=base_ref_by_cycle[str(tick)],
            )

        result = supervisor.run_continuous(
            active_lease,
            cycle_inputs=cycle_inputs,
            cycle_id_prefix=lambda tick: f"host-{config.instance_id}-{tick}",
            stop_event=stop_event,
            interval_seconds=config.poll_interval_seconds,
            lease_ttl_seconds=config.lease_ttl_seconds,
            max_cycles=config.max_cycles_per_process,
            on_cycle=on_cycle,
        )
        return result
    finally:
        reason = ("host loop stopped: " + result.stop_reason
                  if result is not None else "host loop exited before completing")
        store.set_supervisor_mode(
            active_lease, "parked", event_id="host-loop-park-" + uuid.uuid4().hex,
            reason=reason,
        )
        store.release_head(active_lease)


def park_configured_host(store: StateStore, lease: HeadLease, stop_event: Event, *,
                         reason: str) -> bool:
    """Durably prevent future dispatch and signal the local bounded loop to stop.

    This does not forcibly terminate a running maker. Its managed invocation
    remains bounded by the configured runtime timeout and Windows Job Object.
    """
    if not isinstance(store, StateStore) or not isinstance(lease, HeadLease):
        raise ValueError("parking requires StateStore and the current HEAD lease")
    if not isinstance(stop_event, Event):
        raise ValueError("stop_event must be a threading.Event")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("parking requires an operator-visible reason")
    store.record_event(
        lease, "host-park-request-" + uuid.uuid4().hex,
        {"type": "supervisor.park_requested", "reason": reason.strip()},
    )
    stop_event.set()
    return True
