"""Explicit project inspection and inactive installation commands."""

import argparse
from dataclasses import asdict, replace
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import uuid
from threading import Event
from threading import Thread

from .installer import (InstallationConflict, install_devdepartment_sidecar,
                        install_fresh_project, upgrade_codexdevteam_project)
from .host_config import WindowsHostConfig
from .host_runtime import load_host_runtime, load_runtime_capacity, _read_object
from .host_runner import (activate_fresh_host, request_host_stop,
                          run_configured_host_loop, watch_host_stop_request)
from .installer_resources import default_framework_files, devdepartment_sidecar_files
from .handover import (_validate_stageable_preview, handover_map_template,
                      plan_handover, stage_handover)
from .onboarding import OnboardingMode, inspect_project
from .plan_markdown import parse_plan_markdown
from .plan_authoring import publish_plan_once, render_plan_prompt, validate_plan_response
from .dispatch import TaskClassPolicy
from .registry import WorkerRegistry
from .runtime import CodexExecAdapter, request_for_worker
from .state import LeaseError, StateStore
from .tasks import TaskState
from .usage import UsageRate, summarize_invocations


def _assert_supervised_run_ready(config: WindowsHostConfig) -> None:
    """Fail before model spend or project writes if worker and capacity policy is incomplete."""
    bindings = load_host_runtime(config)
    observations = load_runtime_capacity(config, bindings)
    active = set(bindings.registry.active)
    missing = sorted(active - set(observations))
    unknown = sorted(set(observations) - set(bindings.registry.defined))
    unavailable = sorted(worker_id for worker_id in active
                         if worker_id in observations
                         and not observations[worker_id].eligible(now=time.time()))
    reasons = []
    if missing:
        reasons.append("missing capacity observations for " + ", ".join(missing))
    if unknown:
        reasons.append("capacity names undefined workers " + ", ".join(unknown))
    if unavailable:
        reasons.append("workers unavailable or stale " + ", ".join(unavailable))
    if reasons:
        raise ValueError("supervised run preflight failed: " + "; ".join(reasons))


def _create_plan_from_brief(root: Path, config: WindowsHostConfig,
                            brief_path: Path, *, supervised_run: bool = False
                            ) -> dict[str, object]:
    inspection = inspect_project(root)
    marker_path = root / ".codexdevteam" / "installation.json"
    if (inspection.mode is not OnboardingMode.CODEXDEVTEAM_UPGRADE
            or inspection.devdepartment_present or marker_path.is_symlink()
            or not marker_path.is_file()):
        raise ValueError("host-plan requires an installed fresh project without DEVDEPARTMENT")
    marker = _read_object(marker_path, "installation marker")
    if (marker.get("integration_mode") != "fresh" or marker.get("activated") is not False
            or marker.get("active_head") is not None):
        raise ValueError("host-plan requires an inactive fresh installation")
    if config.state_db.exists() or config.state_db.is_symlink():
        raise ValueError("host-plan requires a fresh project before task-state bootstrap")
    if supervised_run:
        _assert_supervised_run_ready(config)
    plan_path = root / "PLAN.md"
    if plan_path.exists() or plan_path.is_symlink():
        raise ValueError("PLAN.md already exists; refusing to overwrite project state")
    registry_data = _read_object(config.registry, "worker registry")
    registry = WorkerRegistry.from_dict(registry_data)
    candidate = registry.candidate_head()
    if candidate is None:
        raise ValueError("worker registry must configure a head_candidate")
    if candidate.identity.role not in {"planner", "head"}:
        raise ValueError("head_candidate role must be planner or head")
    if candidate.identity.runtime != "codex":
        raise ValueError("no host planner adapter is bound for configured runtime "
                         + repr(candidate.identity.runtime))
    if candidate.identity.model.casefold().startswith(("configure-", "placeholder")):
        raise ValueError("configure a concrete planner model ID before planning")
    routing = TaskClassPolicy.from_file(
        root / ".codexdevteam" / "framework" / "task-routing.json")
    unknown_floors = sorted(set(routing.capability_floors.values())
                            - set(registry.capability_order))
    if unknown_floors:
        raise ValueError("task routing uses capability floors absent from registry order: "
                         + ", ".join(unknown_floors))
    checker_roles = {config.checker_role}
    if config.checker_role == "reviewer":
        checker_roles.add("judgment")
    if set(routing.roles.values()) & checker_roles:
        raise ValueError("task routing cannot assign maker tasks to configured checker roles")
    defined_roles = {worker.identity.role for worker in registry.defined.values()}
    if set(routing.roles.values()) - defined_roles:
        raise ValueError("task routing names roles absent from the worker registry")
    if brief_path.is_symlink() or not brief_path.is_file():
        raise ValueError("--brief must name an existing regular UTF-8 file")
    try:
        brief = brief_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read project brief: {exc}") from exc
    request = request_for_worker(
        registry, candidate.identity.unit_id,
        invocation_id="planner-" + uuid.uuid4().hex,
        task_id=None, purpose="head",
        prompt=render_plan_prompt(
            brief, task_routing=routing, fallback_role=config.maker_role),
        working_directory=str(root), timeout_seconds=900, writable=False,
    )
    result = CodexExecAdapter().invoke(request)
    if result.status != "succeeded" or result.truncated:
        raise ValueError("planning invocation did not complete cleanly: "
                         f"status={result.status}, exit_code={result.exit_code}, "
                         f"truncated={result.truncated}; {result.stderr[:1000]}")
    markdown = validate_plan_response(
        result.stdout, allowed_task_classes=set(routing.capability_floors))
    generated_at = datetime.now(timezone.utc).isoformat()
    brief_sha256 = hashlib.sha256(brief.encode("utf-8")).hexdigest()
    response_sha256 = hashlib.sha256(result.stdout.encode("utf-8")).hexdigest()
    provenance = {
        "protocol_version": 1,
        "invocation_id": result.invocation_id,
        "generated_at": generated_at,
        "planner": {"unit_id": result.unit_id, "role": result.role,
                    "runtime": result.runtime, "model": result.model},
        "brief_sha256": brief_sha256,
        "response_sha256": response_sha256,
        "validated_plan_body_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
    }
    markdown = ("<!-- CODEXDEVTEAM_PLAN_PROVENANCE\n"
                + json.dumps(provenance, sort_keys=True, separators=(",", ":"))
                + "\n-->\n\n" + markdown)
    plan_path = publish_plan_once(root, markdown)
    return {
        "plan_path": str(plan_path),
        "task_count": len(parse_plan_markdown(markdown).tasks),
        "invocation_id": result.invocation_id,
        "brief_sha256": brief_sha256,
        "response_sha256": response_sha256,
        "output_sha256": result.output_sha256,
        "generated_at": generated_at,
        "activation_state": "parked",
    }


def _bootstrap_plan(config: WindowsHostConfig, *, takeover_confirmed: bool) -> dict[str, object]:
    root = config.project_root
    inspection = inspect_project(root)
    marker_path = root / ".codexdevteam" / "installation.json"
    if (inspection.mode is not OnboardingMode.CODEXDEVTEAM_UPGRADE
            or inspection.devdepartment_present or marker_path.is_symlink()
            or not marker_path.is_file()):
        raise ValueError("host-bootstrap requires an installed fresh project without DEVDEPARTMENT")
    marker = _read_object(marker_path, "installation marker")
    if (marker.get("integration_mode") != "fresh" or marker.get("activated") is not False
            or marker.get("active_head") is not None):
        raise ValueError("host-bootstrap requires an inactive fresh installation")
    plan_path = root / "PLAN.md"
    if plan_path.is_symlink() or not plan_path.is_file():
        raise ValueError("host-bootstrap requires a regular PLAN.md")
    try:
        plan_bytes = plan_path.read_bytes()
        parsed = parse_plan_markdown(plan_bytes.decode("utf-8"))
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read UTF-8 PLAN.md: {exc}") from exc
    if parsed.findings:
        raise ValueError("PLAN.md has parser findings: " + "; ".join(parsed.findings))
    if any(task.assigned_worker is not None for task in parsed.tasks):
        raise ValueError("fresh bootstrap requires unassigned PLAN tasks; map assignments explicitly")
    tasks = tuple(replace(task, assigned_worker=None, maker_identity=None)
                  for task in parsed.tasks if task.state in {TaskState.PENDING, TaskState.BLOCKED})
    if len(tasks) != len(parsed.tasks):
        raise ValueError("PLAN.md contains in-progress or completed task records; "
                         "explicit migration/review evidence is required")
    if config.state_db.is_symlink():
        raise ValueError("host state database cannot be a symlink")
    if config.state_db.exists() and not config.state_db.is_file():
        raise ValueError("host state database must be a regular file")
    store = StateStore(config.state_db)
    lease = store.acquire_head(
        config.system_id, "bootstrap-" + uuid.uuid4().hex[:12],
        ttl_seconds=config.lease_ttl_seconds,
        takeover_confirmed=takeover_confirmed,
    )
    try:
        changed = store.bootstrap_fresh_tasks(
            lease, tasks, parsed.archived_task_ids,
            source_plan_sha256=hashlib.sha256(plan_bytes).hexdigest(),
            event_id="fresh-plan-bootstrap:" + hashlib.sha256(plan_bytes).hexdigest(),
        )
    finally:
        store.release_head(lease)
    return {"bootstrapped": changed, "task_count": len(tasks),
            "activation_state": "parked"}


def main() -> int:
    parser = argparse.ArgumentParser(prog="codexdevteam",
                                     description="Inspect or install CODEXDEVTEAM without activation")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="read project markers without writing")
    inspect.add_argument("--project", default=".")
    init = sub.add_parser("init", help="install inactive defaults in a fresh or DEVDEPARTMENT project")
    init.add_argument("--project", default=".")
    upgrade = sub.add_parser("upgrade", help="synchronize cleanly managed framework files")
    upgrade.add_argument("--project", default=".")
    handover = sub.add_parser(
        "handover-plan", help="preview explicit legacy task mappings without importing or activating")
    handover.add_argument("--project", default=".")
    handover.add_argument("--mapping", required=True,
                          help="reviewed JSON task mapping bound to the current PLAN hash")
    handover.add_argument("--registry", required=True,
                          help="target CODEXDEVTEAM worker registry JSON")
    handover_stage = sub.add_parser(
        "handover-stage", help="stage a lossless task translation in inactive target state")
    handover_stage.add_argument("--project", default=".")
    handover_stage.add_argument("--mapping", required=True,
                                help="reviewed JSON task mapping bound to the current PLAN hash")
    handover_stage.add_argument("--registry", required=True,
                                help="target CODEXDEVTEAM worker registry JSON")
    handover_stage.add_argument("--state-db", required=True,
                                help="target CODEXDEVTEAM SQLite state path")
    handover_stage.add_argument("--event-id",
                                help="idempotency key; generated from the source and mapping hashes by default")
    handover_stage.add_argument("--confirm-target-takeover", action="store_true",
                                help="confirm takeover of an expired prior lease in the target state DB")
    handover_template = sub.add_parser(
        "handover-map-template", help="emit an incomplete task-map draft for review")
    handover_template.add_argument("--project", default=".")
    usage = sub.add_parser("usage", help="summarize recorded invocation outcomes and configured costs")
    usage.add_argument("--state-db", required=True)
    usage.add_argument("--rates", help="JSON file with configured provider/model pricing rates")
    host_config = sub.add_parser(
        "host-config", help="validate and display the inactive Windows supervisor configuration")
    host_config.add_argument("--project", default=".")
    host_preflight = sub.add_parser(
        "host-preflight", help="check host bindings and capacity without activating or dispatching")
    host_preflight.add_argument("--project", default=".")
    host_status = sub.add_parser(
        "host-status", help="read supervisor mode, lease expiry, and task counts")
    host_status.add_argument("--project", default=".")
    host_stop = sub.add_parser(
        "host-stop", help="request the running Windows host to park after its current closeout")
    host_stop.add_argument("--project", default=".")
    host_stop.add_argument("--reason", required=True)
    host_run = sub.add_parser(
        "host-run", help="explicitly activate and run a bounded Windows supervisor session")
    host_run.add_argument("--project", default=".")
    host_run.add_argument("--confirm-activation", action="store_true",
                          help="confirm fresh-project HEAD activation and supervised dispatch")
    host_run.add_argument("--brief", help="start a fresh project directly from this UTF-8 brief")
    host_run.add_argument("--confirm-plan-write", action="store_true",
                          help="confirm exclusive creation of PLAN.md from --brief")
    host_run.add_argument("--confirm-prior-lease-takeover", "--takeover-expired-lease",
                          dest="takeover_expired_lease", action="store_true",
                          help="confirm takeover of a prior HEAD lease record, including bootstrap")
    host_bootstrap = sub.add_parser(
        "host-bootstrap", help="seed a fresh inactive task store from PLAN.md")
    host_bootstrap.add_argument("--project", default=".")
    host_bootstrap.add_argument("--confirm-prior-lease-takeover", "--takeover-expired-lease",
                                dest="takeover_expired_lease", action="store_true",
                                help="confirm takeover of a prior HEAD lease record")
    host_plan = sub.add_parser(
        "host-plan", help="draft and validate a fresh-project PLAN from a project brief")
    host_plan.add_argument("--project", default=".")
    host_plan.add_argument("--brief", required=True, help="UTF-8 project brief file")
    host_plan.add_argument("--confirm-write", action="store_true",
                           help="allow exclusive creation of PLAN.md after validation")
    args = parser.parse_args()
    root = Path(getattr(args, "project", ".")).resolve()
    try:
        if args.command == "inspect":
            result = inspect_project(root)
            print(json.dumps({"mode": result.mode.value,
                              "codexdevteam_present": result.codexdevteam_present,
                              "devdepartment_present": result.devdepartment_present,
                              "activation_allowed": result.activation_allowed,
                              "findings": list(result.findings)}, indent=2))
            return 0 if result.mode is not OnboardingMode.CONFLICT else 2
        if args.command == "usage":
            raw_database = Path(args.state_db)
            if raw_database.is_symlink() or not raw_database.is_file():
                raise ValueError("--state-db must name an existing regular file")
            database = raw_database.resolve(strict=True)
            rates: tuple[UsageRate, ...] = ()
            if args.rates:
                raw_rates_path = Path(args.rates)
                if raw_rates_path.is_symlink() or not raw_rates_path.is_file():
                    raise ValueError("--rates must name an existing regular file")
                rates_path = raw_rates_path.resolve(strict=True)
                raw_rates = json.loads(rates_path.read_text(encoding="utf-8"))
                if not isinstance(raw_rates, list):
                    raise ValueError("rate configuration must be a JSON array")
                rates = tuple(UsageRate(**item) for item in raw_rates
                              if isinstance(item, dict))
                if len(rates) != len(raw_rates):
                    raise ValueError("every configured usage rate must be an object")
            with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
                rows = db.execute("SELECT payload_json FROM state_events").fetchall()
            events = [{"payload": json.loads(row[0])} for row in rows]
            report = summarize_invocations(events, rates=rates)
            print(json.dumps([asdict(item) for item in report], indent=2, sort_keys=True))
            return 0
        if args.command == "host-config":
            config = WindowsHostConfig.load(root)
            print(json.dumps({
                "valid": True,
                "activation_state": "parked",
                "configuration": config.to_dict(),
                "runner_available": False,
            }, indent=2, sort_keys=True))
            return 0
        if args.command == "host-preflight":
            config = WindowsHostConfig.load(root)
            findings: list[str] = []
            bindings = None
            observations = {}
            try:
                bindings = load_host_runtime(config)
            except (OSError, ValueError, TypeError) as exc:
                findings.append("runtime bindings: " + str(exc))
            if bindings is not None:
                try:
                    observations = load_runtime_capacity(config, bindings)
                except (OSError, ValueError, TypeError) as exc:
                    findings.append("capacity source: " + str(exc))
                unknown = sorted(set(observations) - set(bindings.registry.defined))
                if unknown:
                    findings.append("capacity names undefined workers: " + ", ".join(unknown))
                missing = sorted(set(bindings.registry.active) - set(observations))
                if missing:
                    findings.append("capacity missing active workers: " + ", ".join(missing))
                stale = sorted(worker_id for worker_id in bindings.registry.active
                               if worker_id in observations
                               and not observations[worker_id].eligible(now=time.time()))
                if stale:
                    findings.append("capacity unavailable or stale for active workers: "
                                    + ", ".join(stale))
            report = {
                "worker_and_capacity_ready": not findings,
                "activation_state": "parked",
                "activation_available": False,
                "dispatch_performed": False,
                "findings": findings,
                "active_worker_ids": list(bindings.registry.active) if bindings else [],
            }
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0
        if args.command == "host-status":
            config = WindowsHostConfig.load(root)
            database = config.state_db
            if database.is_symlink():
                raise ValueError("host state database cannot be a symlink")
            if not database.is_file():
                print(json.dumps({"initialized": False, "mode": "parked",
                                  "lease_active": False, "tasks_by_state": {}},
                                 indent=2, sort_keys=True))
                return 0
            with closing(sqlite3.connect(
                    f"{database.resolve(strict=True).as_uri()}?mode=ro", uri=True)) as db:
                db.row_factory = sqlite3.Row
                mode_row = db.execute(
                    "SELECT mode, reason, changed_at, generation FROM supervisor_mode "
                    "WHERE singleton=1").fetchone()
                lease_row = db.execute(
                    "SELECT system_id, instance_id, generation, expires_at FROM head_lease "
                    "WHERE singleton=1").fetchone()
                counts: dict[str, int] = {}
                for row in db.execute("SELECT payload_json FROM tasks").fetchall():
                    state = json.loads(row["payload_json"]).get("state")
                    if isinstance(state, str):
                        counts[state] = counts.get(state, 0) + 1
                running_invocations = db.execute(
                    "SELECT COUNT(*) FROM task_invocation_liveness WHERE state='running'"
                ).fetchone()[0]
            now = time.time()
            lease_active = bool(lease_row and lease_row["expires_at"] > now)
            mode = dict(mode_row) if mode_row else {
                "mode": "parked", "reason": "not started", "changed_at": None,
                "generation": None,
            }
            print(json.dumps({
                "initialized": True,
                "mode": mode["mode"],
                "reason": mode["reason"],
                "changed_at": mode["changed_at"],
                "mode_generation": mode["generation"],
                "lease_active": lease_active,
                "stop_requested": (config.control_root / "STOP.json").is_file(),
                "lease_owner": ({"system_id": lease_row["system_id"],
                                 "instance_id": lease_row["instance_id"],
                                 "generation": lease_row["generation"],
                                 "expires_at": lease_row["expires_at"]}
                                if lease_row else None),
                "running_maker_invocations": running_invocations,
                "tasks_by_state": counts,
            }, indent=2, sort_keys=True))
            return 0
        if args.command == "host-run":
            config = WindowsHostConfig.load(root)
            if not args.confirm_activation:
                raise ValueError("host-run requires --confirm-activation")
            bootstrapped_in_this_command = False
            if args.brief:
                if not args.confirm_plan_write:
                    raise ValueError("host-run --brief requires --confirm-plan-write")
                _create_plan_from_brief(root, config, Path(args.brief), supervised_run=True)
                _bootstrap_plan(config, takeover_confirmed=args.takeover_expired_lease)
                bootstrapped_in_this_command = True
            elif args.confirm_plan_write:
                raise ValueError("--confirm-plan-write is valid only with --brief")
            if config.state_db.is_symlink() or not config.state_db.is_file():
                raise ValueError("host-run requires an existing initialized task state database")
            store = StateStore(config.state_db)
            if not store.list_tasks():
                raise ValueError("host-run requires tasks in the authoritative state database")
            lease = activate_fresh_host(
                config, store, confirmed=True,
                takeover_confirmed=(args.takeover_expired_lease or bootstrapped_in_this_command),
            )
            stop_event = Event()
            watcher_stop = Event()
            watcher = Thread(
                target=watch_host_stop_request,
                args=(config, store, lease, stop_event, watcher_stop),
                name="codexdevteam-host-stop-watch", daemon=True,
            )
            try:
                watcher.start()
                result = run_configured_host_loop(config, store, lease, stop_event)
            except BaseException:
                try:
                    store.set_supervisor_mode(
                        lease, "parked", event_id="host-run-failure-" + uuid.uuid4().hex,
                        reason="host-run exited before normal closeout",
                    )
                    store.release_head(lease)
                except Exception:
                    pass
                raise
            finally:
                watcher_stop.set()
                if watcher.ident is not None:
                    watcher.join(timeout=2.0)
            last_cycle = result.last_cycle
            print(json.dumps({
                "cycles_completed": result.cycles_completed,
                "stop_reason": result.stop_reason,
                "last_cycle_id": (last_cycle.dispatch.cycle_id if last_cycle else None),
                "tasks_launched": ([maker.task_id for maker in last_cycle.makers]
                                   if last_cycle else []),
                "activation_state": "parked",
            }, indent=2, sort_keys=True))
            return 0
        if args.command == "host-stop":
            config = WindowsHostConfig.load(root)
            request_id = request_host_stop(config, reason=args.reason)
            print(json.dumps({"stop_request_id": request_id,
                              "status": "pending", "activation_state": "unchanged"},
                             indent=2, sort_keys=True))
            return 0
        if args.command == "host-bootstrap":
            config = WindowsHostConfig.load(root)
            print(json.dumps(_bootstrap_plan(
                config, takeover_confirmed=args.takeover_expired_lease),
                indent=2, sort_keys=True))
            return 0
        if args.command == "host-plan":
            if not args.confirm_write:
                raise ValueError("host-plan requires --confirm-write before it can create PLAN.md")
            config = WindowsHostConfig.load(root)
            print(json.dumps(_create_plan_from_brief(root, config, Path(args.brief)),
                             indent=2, sort_keys=True))
            return 0
        if args.command == "handover-plan":
            inspection = inspect_project(root)
            if (not inspection.devdepartment_present
                    or inspection.mode is not OnboardingMode.DEVDEPARTMENT_SIDECAR):
                raise ValueError("handover-plan requires a project with DEVDEPARTMENT present")
            plan_path = root / "PLAN.md"
            if plan_path.is_symlink() or not plan_path.is_file():
                raise ValueError("source project PLAN.md must be a regular file")
            mapping_path = Path(args.mapping)
            registry_path = Path(args.registry)
            for label, path in (("--mapping", mapping_path), ("--registry", registry_path)):
                if path.is_symlink() or not path.is_file():
                    raise ValueError(f"{label} must name an existing regular file")
            mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
            registry_data = json.loads(registry_path.read_text(encoding="utf-8"))
            preview = plan_handover(
                plan_path.read_bytes(), mapping,
                registry=WorkerRegistry.from_dict(registry_data),
            )
            print(json.dumps(preview.to_dict(), indent=2, sort_keys=True))
            return 0
        if args.command == "handover-stage":
            inspection = inspect_project(root)
            if (not inspection.devdepartment_present
                    or inspection.mode is not OnboardingMode.DEVDEPARTMENT_SIDECAR):
                raise ValueError("handover-stage requires an inactive DEVDEPARTMENT sidecar project")
            plan_path = root / "PLAN.md"
            if plan_path.is_symlink() or not plan_path.is_file():
                raise ValueError("source project PLAN.md must be a regular file")
            mapping_path, registry_path = Path(args.mapping), Path(args.registry)
            for label, path in (("--mapping", mapping_path), ("--registry", registry_path)):
                if path.is_symlink() or not path.is_file():
                    raise ValueError(f"{label} must name an existing regular file")
            source_bytes = plan_path.read_bytes()
            mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
            registry_data = json.loads(registry_path.read_text(encoding="utf-8"))
            registry = WorkerRegistry.from_dict(registry_data)
            preview = plan_handover(source_bytes, mapping, registry=registry)
            _validate_stageable_preview(preview)
            raw_database = Path(args.state_db)
            if raw_database.is_symlink():
                raise ValueError("--state-db cannot be a symlink")
            if raw_database.exists() and not raw_database.is_file():
                raise ValueError("--state-db must name a regular file or a new file path")
            database = raw_database.resolve()
            store = StateStore(database)
            lease = store.acquire_head(
                "CODEXDEVTEAM", "handover-stage-" + uuid.uuid4().hex,
                takeover_confirmed=args.confirm_target_takeover,
            )
            event_id = args.event_id or (
                "handover:" + preview.source_plan_sha256[:20] + ":" + preview.mapping_sha256[:20]
            )
            try:
                staged = stage_handover(
                    source_bytes, mapping, registry=registry, store=store, lease=lease,
                    event_id=event_id,
                )
            finally:
                store.release_head(lease)
            print(json.dumps({
                "event_id": event_id,
                "source_plan_sha256": staged.source_plan_sha256,
                "mapping_sha256": staged.mapping_sha256,
                "staged_task_ids": [task.task_id for task in staged.tasks],
                "historical_task_ids": list(staged.historical_task_ids),
                "target_lease_released": True,
                "source_process_fenced": False,
                "activation_authorized": False,
            }, indent=2, sort_keys=True))
            return 0
        if args.command == "handover-map-template":
            inspection = inspect_project(root)
            if (not inspection.devdepartment_present
                    or inspection.mode is not OnboardingMode.DEVDEPARTMENT_SIDECAR):
                raise ValueError("handover-map-template requires a project with DEVDEPARTMENT present")
            plan_path = root / "PLAN.md"
            if plan_path.is_symlink() or not plan_path.is_file():
                raise ValueError("source project PLAN.md must be a regular file")
            draft = handover_map_template(plan_path.read_bytes())
            print(json.dumps(draft, indent=2, sort_keys=True))
            return 0
        if args.command == "upgrade":
            marker = json.loads((root / ".codexdevteam" / "installation.json").read_text(
                encoding="utf-8"))
            if marker.get("integration_mode") == "devdepartment_sidecar":
                files = devdepartment_sidecar_files()
            else:
                files = default_framework_files()
            changed = upgrade_codexdevteam_project(root, files)
            print("Updated managed files: " + (", ".join(changed) if changed else "already current"))
            return 0
        mode = inspect_project(root).mode
        if mode is OnboardingMode.FRESH:
            destination = install_fresh_project(root, default_framework_files())
        elif mode is OnboardingMode.DEVDEPARTMENT_SIDECAR:
            destination = install_devdepartment_sidecar(root, devdepartment_sidecar_files())
        else:
            raise InstallationConflict(
                f"cannot init project in {mode.value} mode; inspect it and use the explicit upgrade/handover workflow"
            )
        print(f"Installed inactive CODEXDEVTEAM metadata at {destination}")
        print("No HEAD lease was acquired. Configuration, live verification, and activation remain separate steps.")
        return 0
    except (OSError, ValueError, TypeError, RuntimeError, sqlite3.Error, LeaseError,
            InstallationConflict) as exc:
        print(f"CODEXDEVTEAM {args.command} failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
