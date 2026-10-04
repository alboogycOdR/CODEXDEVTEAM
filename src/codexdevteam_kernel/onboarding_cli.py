"""Explicit project inspection and inactive installation commands."""

import argparse
from dataclasses import asdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import uuid

from .installer import (InstallationConflict, install_devdepartment_sidecar,
                        install_fresh_project, upgrade_codexdevteam_project)
from .installer_resources import default_framework_files, devdepartment_sidecar_files
from .handover import (_validate_stageable_preview, handover_map_template,
                      plan_handover, stage_handover)
from .onboarding import OnboardingMode, inspect_project
from .registry import WorkerRegistry
from .state import LeaseError, StateStore
from .usage import UsageRate, summarize_invocations


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
    except (OSError, ValueError, TypeError, sqlite3.Error, LeaseError,
            InstallationConflict) as exc:
        print(f"CODEXDEVTEAM {args.command} failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
