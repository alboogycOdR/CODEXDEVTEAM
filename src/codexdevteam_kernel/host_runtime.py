"""Fail-closed loader for configured host workers and mechanical gate policy."""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .dispatch import TaskClassPolicy
from .capacity_source import load_host_capacity as _load_host_capacity
from .gate import GateRunner
from .host_config import WindowsHostConfig, _reject_duplicate_keys
from .registry import WorkerRegistry
from .runtime import CodexExecAdapter
from .supervisor import InvocationAdapter
from .territory import normalize_repo_path


@dataclass(frozen=True, slots=True)
class HostRuntimeBindings:
    registry: WorkerRegistry
    adapters: dict[str, InvocationAdapter]
    gate_commands: dict[str, tuple[str, ...] | None]
    protected_paths: tuple[str, ...]
    environment_allowlist: tuple[str, ...]
    task_class_policy: TaskClassPolicy
    ignored_paths_allowlist: tuple[str, ...]


def load_host_runtime(config: WindowsHostConfig, *,
                      codex_executable: str = "codex") -> HostRuntimeBindings:
    """Load only strict, explicitly configured worker and gate bindings.

    This function creates adapters but launches no process and does not acquire
    a HEAD lease. Unsupported configured runtimes fail closed; no substitution
    or provider fallback is attempted.
    """
    if not isinstance(config, WindowsHostConfig):
        raise ValueError("host runtime loading requires WindowsHostConfig")
    registry_data = _read_object(config.registry, "worker registry")
    registry = WorkerRegistry.from_dict(registry_data)
    routing_path = config.project_root / ".codexdevteam" / "framework" / "task-routing.json"
    task_class_policy = TaskClassPolicy.from_file(routing_path)
    unknown_floors = sorted(set(task_class_policy.capability_floors.values())
                            - set(registry.capability_order))
    if unknown_floors:
        raise ValueError("task routing uses capability floors absent from registry order: "
                         + ", ".join(unknown_floors))
    checker_roles = {config.checker_role}
    if config.checker_role == "reviewer":
        checker_roles.add("judgment")
    unsafe_roles = sorted(set(task_class_policy.roles.values()) & checker_roles)
    if unsafe_roles:
        raise ValueError("task routing cannot assign maker tasks to checker roles: "
                         + ", ".join(unsafe_roles))
    defined_roles = {worker.identity.role for worker in registry.defined.values()}
    unknown_roles = sorted(set(task_class_policy.roles.values()) - defined_roles)
    if unknown_roles:
        raise ValueError("task routing names roles absent from the worker registry: "
                         + ", ".join(unknown_roles))
    active_roles = {worker.identity.role for worker in registry.active_workers()}
    inactive_routes = sorted(set(task_class_policy.roles.values()) - active_roles)
    if inactive_routes:
        raise ValueError("task routing has no active workers for roles: "
                         + ", ".join(inactive_routes))
    active_workers = registry.active_workers()
    if len(active_workers) < 2:
        raise ValueError("host operation requires at least two active strict workers for maker/checker separation")
    maker_workers = [worker for worker in active_workers
                     if worker.identity.role == config.maker_role]
    checker_roles = {config.checker_role}
    if config.checker_role == "reviewer":
        checker_roles.add("judgment")
    checker_workers = [worker for worker in active_workers
                       if worker.identity.role in checker_roles]
    if not maker_workers or not checker_workers:
        raise ValueError("active registry must include configured maker and checker roles")
    independent_pair = any(
        left.identity.unit_id != right.identity.unit_id
        and (left.identity.runtime, left.identity.model)
        != (right.identity.runtime, right.identity.model)
        for left in maker_workers
        for right in checker_workers
    )
    if not independent_pair:
        raise ValueError("active registry has no mechanically independent maker/checker identity pair")
    adapters: dict[str, InvocationAdapter] = {}
    for worker in active_workers:
        if worker.control_mode != "strict":
            raise ValueError(f"active worker {worker.identity.unit_id} lacks strict verification")
        if worker.identity.runtime == "codex":
            adapters.setdefault(worker.identity.runtime, CodexExecAdapter(codex_executable))
        else:
            raise ValueError(
                f"no host runtime adapter is bound for configured runtime "
                f"{worker.identity.runtime!r} (worker {worker.identity.unit_id})")

    verification = _read_object(config.verification_config, "verification policy")
    allowed = {"protocol_version", "protected_paths", "commands",
               "environment_allowlist", "strict_supervision",
               "ignored_paths_allowlist"}
    unknown = set(verification) - allowed
    if unknown:
        raise ValueError("unknown verification policy fields: " + ", ".join(sorted(unknown)))
    if verification.get("protocol_version") != 1:
        raise ValueError("unsupported verification policy protocol_version")
    if verification.get("strict_supervision") is not True:
        raise ValueError("verification policy must enable strict_supervision")
    protected = verification.get("protected_paths")
    if (not isinstance(protected, list) or not protected
            or not all(isinstance(item, str) and item.strip() for item in protected)):
        raise ValueError("verification policy protected_paths must be a non-empty string array")
    environment = verification.get("environment_allowlist")
    if (not isinstance(environment, list)
            or not all(isinstance(item, str) and item.strip() for item in environment)):
        raise ValueError("verification policy environment_allowlist must be a string array")
    ignored_paths = verification.get("ignored_paths_allowlist", [])
    if (not isinstance(ignored_paths, list)
            or not all(isinstance(item, str) and item.strip() for item in ignored_paths)):
        raise ValueError("verification policy ignored_paths_allowlist must be a string array")
    try:
        ignored_paths_allowlist = tuple(normalize_repo_path(item) for item in ignored_paths)
    except ValueError as exc:
        raise ValueError("verification policy ignored_paths_allowlist has an unsafe path pattern") from exc
    for pattern in ignored_paths_allowlist:
        parts = pattern.split("/")
        if (not any(not any(symbol in part for symbol in "*?[") for part in parts)
                or any(part.casefold() == ".git" for part in parts)
                or parts[0].casefold() == ".codexdevteam"):
            raise ValueError("ignored path patterns must be narrow and cannot cover Git or host metadata")
    if len(set(ignored_paths_allowlist)) != len(ignored_paths_allowlist):
        raise ValueError("verification policy ignored_paths_allowlist cannot contain duplicates")
    raw_commands = verification.get("commands")
    required_commands = {"build", "typecheck", "test_full"}
    if not isinstance(raw_commands, dict) or set(raw_commands) != required_commands:
        raise ValueError("verification policy must define build, typecheck, and test_full commands")
    commands: dict[str, tuple[str, ...] | None] = {}
    for name, argv in raw_commands.items():
        if (not isinstance(argv, list) or not argv
                or not all(isinstance(part, str) and part for part in argv)):
            raise ValueError(f"verification command {name} must be a non-empty argv array")
        commands[name] = tuple(argv)
    return HostRuntimeBindings(registry, adapters, commands, tuple(protected),
                               tuple(environment), task_class_policy,
                               ignored_paths_allowlist)


def load_runtime_capacity(config: WindowsHostConfig,
                          bindings: HostRuntimeBindings):
    """Refresh capacity from the configured provider source using host bindings."""
    if not isinstance(bindings, HostRuntimeBindings):
        raise ValueError("capacity refresh requires validated host runtime bindings")
    adapter = bindings.adapters.get("codex")
    executable = getattr(adapter, "executable", None)
    if config.capacity_source == "codex_app_server" and not isinstance(executable, str):
        raise ValueError("Codex capacity source requires the configured Codex executable")
    command_prefix = (executable,) if isinstance(executable, str) else ()
    return _load_host_capacity(config, bindings.registry, command_prefix=command_prefix)


def build_gate_runner(project_root: str | Path, config: WindowsHostConfig,
                      bindings: HostRuntimeBindings) -> GateRunner:
    """Create the mechanical gate with configured protected paths and artifacts."""
    if not isinstance(bindings, HostRuntimeBindings):
        raise ValueError("gate construction requires validated host runtime bindings")
    return GateRunner(project_root, config.project_root / ".codexdevteam" / "gates",
                      protected_paths=bindings.protected_paths)


def _read_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be an existing regular file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"),
                             object_pairs_hook=_reject_duplicate_keys)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload
