"""Packaged, inactive-by-default CODEXDEVTEAM installation files."""

import json


def default_framework_files() -> dict[str, str]:
    """Return the minimal fresh-project framework bundle; no task or HEAD is activated."""
    registry = {
        "protocol_version": 1,
        "head_candidate": "codex-head",
        "active": [],
        "defined": {
            "codex-head": {
                "role": "planner",
                "capability_floor": "frontier",
                "runtime": "codex",
                "model": "configure-model-before-activation",
            }
        },
        "role_policies": {},
        "capability_order": ["standard", "advanced", "frontier"],
    }
    verification = {
        "protocol_version": 1,
        "protected_paths": ["PLAN.md", ".codexdevteam/**"],
        "commands": {"build": None, "typecheck": None, "test_full": None},
        "environment_allowlist": [],
        "ignored_paths_allowlist": [],
        "strict_supervision": False,
    }
    task_routing = {
        "capability_floors": {
            "mechanical": "standard",
            "standard": "standard",
            "complex": "advanced",
            "critical": "frontier",
        }
    }
    interoperability = {
        "protocol_version": 1,
        "active_head": None,
        "activated": False,
        "devdepartment_policy": "preserve-and-require-explicit-handover",
    }
    supervisor = {
        "protocol_version": 1,
        "activation_state": "parked",
        "state_db": ".codexdevteam/state/state.sqlite",
        "registry": ".codexdevteam/framework/registry.template.json",
        "verification_config": ".codexdevteam/framework/verification.json",
        "capacity_snapshot": ".codexdevteam/control/capacity.json",
        "worktree_root": "../{project_name}-codexdevteam-worktrees",
        "control_root": ".codexdevteam/control",
        "logs_root": ".codexdevteam/logs",
        "system_id": "codexdevteam",
        "instance_id": "configure-host-instance",
        "maker_role": "implementation",
        "checker_role": "reviewer",
        "poll_interval_seconds": 30,
        "lease_ttl_seconds": 90,
        "max_cycles_per_process": 100,
        "max_rework_attempts": 1,
        "require_strict": True,
        "require_capacity_observation": True,
    }
    capacity = {
        "protocol_version": 1,
        "observed_at": 0,
        "stale_after_seconds": 120,
        "workers": {},
    }
    return {
        ".codexdevteam/framework/registry.template.json": _json(registry),
        ".codexdevteam/framework/verification.json": _json(verification),
        ".codexdevteam/framework/task-routing.json": _json(task_routing),
        ".codexdevteam/framework/interoperability.json": _json(interoperability),
        ".codexdevteam/framework/supervisor.json": _json(supervisor),
        ".codexdevteam/framework/capacity.template.json": _json(capacity),
        ".codexdevteam/framework/usage-rates.template.json": "[]\n",
        ".codexdevteam/framework/README.md": (
            "# CODEXDEVTEAM project setup\n\n"
            "Installation is inactive. Configure registry identities and concrete model IDs, "
            "verification commands, worktree policy, and strict-worker live receipts before "
            "requesting an explicit HEAD activation. The configured Codex planner is only a "
            "candidate; no worker is active by default. Do not place secrets in these files.\n"
        ),
    }


def devdepartment_sidecar_files() -> dict[str, str]:
    """Return compatibility metadata only; incumbent framework files remain owned by DEVDEPARTMENT."""
    compatibility = {
        "protocol_version": 1,
        "adapter_version": 1,
        "incumbent_head": "DEVDEPARTMENT",
        "requested_head": "CODEXDEVTEAM",
        "activated": False,
        "handover_required": True,
    }
    return {
        ".codexdevteam/framework/sidecar/compatibility.json": _json(compatibility),
        ".codexdevteam/framework/sidecar/README.md": (
            "# CODEXDEVTEAM compatibility sidecar\n\n"
            "DEVDEPARTMENT remains the active owner. This sidecar does not replace its "
            "scripts, hooks, configuration, or state. Do not activate CODEXDEVTEAM until "
            "a coordinated handover protocol is available and explicitly completed.\n"
        ),
    }


def _json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"
