"""Validated, inactive-by-default Windows supervisor host configuration."""

from dataclasses import dataclass
import json
import math
from pathlib import Path, PurePosixPath
import re
import time
from typing import Any

from .dispatch import CapacityObservation


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_CONFIG_RELATIVE_PATHS = {
    "state_db": (".codexdevteam", "state"),
    "registry": (".codexdevteam", "framework"),
    "verification_config": (".codexdevteam", "framework"),
    "capacity_snapshot": (".codexdevteam", "control"),
    "control_root": (".codexdevteam",),
    "logs_root": (".codexdevteam",),
}


@dataclass(frozen=True, slots=True)
class WindowsHostConfig:
    """Paths and bounded polling policy for a future Windows host runner.

    This contract deliberately has no activation switch. Installation and
    configuration alone can never start a supervisor or acquire HEAD.
    """

    project_root: Path
    state_db: Path
    registry: Path
    verification_config: Path
    capacity_snapshot: Path
    worktree_root: Path
    control_root: Path
    logs_root: Path
    system_id: str
    instance_id: str
    maker_role: str = "implementation"
    checker_role: str = "reviewer"
    poll_interval_seconds: int = 30
    lease_ttl_seconds: int = 90
    max_cycles_per_process: int = 100
    max_rework_attempts: int = 1
    protocol_version: int = 1
    activation_state: str = "parked"
    require_strict: bool = True
    require_capacity_observation: bool = True
    capacity_source: str = "codex_app_server"

    @classmethod
    def from_dict(cls, project_root: str | Path, data: dict[str, Any]
                  ) -> "WindowsHostConfig":
        if not isinstance(data, dict):
            raise ValueError("supervisor configuration must be a JSON object")
        allowed = {
            "protocol_version", "activation_state", "state_db", "registry",
            "verification_config", "capacity_snapshot", "worktree_root", "control_root",
            "logs_root", "system_id", "instance_id", "maker_role", "checker_role",
            "poll_interval_seconds", "lease_ttl_seconds",
            "max_cycles_per_process", "max_rework_attempts", "require_strict",
            "require_capacity_observation", "capacity_source",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError("unknown supervisor configuration fields: "
                             + ", ".join(sorted(unknown)))
        if data.get("protocol_version") != 1:
            raise ValueError("unsupported supervisor configuration protocol_version")
        if data.get("activation_state", "parked") != "parked":
            raise ValueError("host configuration cannot activate supervision; activation remains parked")

        root = Path(project_root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("project_root must be an existing directory")
        resolved: dict[str, Path] = {}
        for field_name, required_prefix in _CONFIG_RELATIVE_PATHS.items():
            raw = data.get(field_name)
            if not isinstance(raw, str) or not raw:
                raise ValueError(f"{field_name} must be a project-relative path")
            pure = PurePosixPath(raw)
            if (pure.is_absolute() or ".." in pure.parts or "\\" in raw
                    or not pure.parts or pure.parts[:len(required_prefix)] != required_prefix):
                raise ValueError(f"{field_name} must stay under .codexdevteam using forward slashes")
            candidate = root.joinpath(*pure.parts)
            current = root
            for part in pure.parts:
                current = current / part
                if current.is_symlink():
                    raise ValueError(f"{field_name} cannot traverse symlinks")
            resolved[field_name] = candidate
        worktree_template = data.get("worktree_root")
        expected_worktree_template = "../{project_name}-codexdevteam-worktrees"
        if worktree_template != expected_worktree_template:
            raise ValueError("worktree_root must use the managed sibling directory template")
        worktree = root.parent / f"{root.name}-codexdevteam-worktrees"
        if worktree.is_symlink():
            raise ValueError("worktree_root cannot be a symlink")
        resolved["worktree_root"] = worktree

        system_id = data.get("system_id")
        instance_id = data.get("instance_id")
        for name, value in (("system_id", system_id), ("instance_id", instance_id)):
            if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"{name} must be a short stable identifier")
        maker_role = data.get("maker_role", "implementation")
        checker_role = data.get("checker_role", "reviewer")
        for name, value in (("maker_role", maker_role), ("checker_role", checker_role)):
            if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"{name} must be a short role identifier")
        if maker_role == checker_role:
            raise ValueError("maker_role and checker_role must be distinct")
        if checker_role not in {"reviewer", "judgment"}:
            raise ValueError("checker_role must be 'reviewer' or 'judgment'")

        def bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
            value = data.get(name, default)
            if (not isinstance(value, int) or isinstance(value, bool)
                    or not minimum <= value <= maximum):
                raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
            return value

        def boolean(name: str, default: bool) -> bool:
            value = data.get(name, default)
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be boolean")
            return value

        if not boolean("require_strict", True):
            raise ValueError("Windows unattended host configuration requires strict workers")
        if not boolean("require_capacity_observation", True):
            raise ValueError("Windows unattended host configuration requires fresh capacity observations")
        capacity_source = data.get("capacity_source", "codex_app_server")
        if (not isinstance(capacity_source, str)
                or capacity_source not in {"codex_app_server", "snapshot"}):
            raise ValueError("capacity_source must be 'codex_app_server' or 'snapshot'")
        return cls(
            project_root=root,
            **resolved,
            system_id=system_id,
            instance_id=instance_id,
            maker_role=maker_role,
            checker_role=checker_role,
            poll_interval_seconds=bounded_int("poll_interval_seconds", 30, 1, 3600),
            lease_ttl_seconds=bounded_int("lease_ttl_seconds", 90, 15, 3600),
            max_cycles_per_process=bounded_int("max_cycles_per_process", 100, 1, 10000),
            max_rework_attempts=bounded_int("max_rework_attempts", 1, 0, 5),
            require_strict=True,
            require_capacity_observation=True,
            capacity_source=capacity_source,
        )

    @classmethod
    def load(cls, project_root: str | Path, path: str | Path | None = None
             ) -> "WindowsHostConfig":
        root = Path(project_root).resolve(strict=True)
        config_path = Path(path) if path is not None else (
            root / ".codexdevteam" / "framework" / "supervisor.json")
        if config_path.is_symlink() or not config_path.is_file():
            raise ValueError("supervisor configuration must be an existing regular file")
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"),
                             object_pairs_hook=_reject_duplicate_keys)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read supervisor configuration: {exc}") from exc
        return cls.from_dict(root, raw)

    def to_dict(self) -> dict[str, object]:
        """Render stable project-relative configuration without host secrets."""
        def relative(path: Path) -> str:
            if path == self.worktree_root:
                return "../{project_name}-codexdevteam-worktrees"
            return path.relative_to(self.project_root).as_posix()

        return {
            "protocol_version": self.protocol_version,
            "activation_state": self.activation_state,
            "state_db": relative(self.state_db),
            "registry": relative(self.registry),
            "verification_config": relative(self.verification_config),
            "capacity_snapshot": relative(self.capacity_snapshot),
            "worktree_root": relative(self.worktree_root),
            "control_root": relative(self.control_root),
            "logs_root": relative(self.logs_root),
            "system_id": self.system_id,
            "instance_id": self.instance_id,
            "maker_role": self.maker_role,
            "checker_role": self.checker_role,
            "poll_interval_seconds": self.poll_interval_seconds,
            "lease_ttl_seconds": self.lease_ttl_seconds,
            "max_cycles_per_process": self.max_cycles_per_process,
            "max_rework_attempts": self.max_rework_attempts,
            "require_strict": self.require_strict,
            "require_capacity_observation": self.require_capacity_observation,
            "capacity_source": self.capacity_source,
        }

    def load_capacity_observations(self, *, now: float | None = None
                                   ) -> dict[str, CapacityObservation]:
        """Load explicit short-lived worker capacity data; never infer availability.

        The snapshot producer is responsible for observing its configured
        runtime/provider. Dispatch still applies the freshness and quota checks
        in ``CapacityObservation.eligible``.
        """
        path = self.capacity_snapshot
        if path.is_symlink() or not path.is_file():
            raise ValueError("capacity snapshot must be an existing regular file")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"),
                                 object_pairs_hook=_reject_duplicate_keys)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read capacity snapshot: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("capacity snapshot must be a JSON object")
        allowed = {"protocol_version", "observed_at", "stale_after_seconds", "workers"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError("unknown capacity snapshot fields: " + ", ".join(sorted(unknown)))
        if payload.get("protocol_version") != 1:
            raise ValueError("unsupported capacity snapshot protocol_version")
        observed_at = payload.get("observed_at")
        stale_after = payload.get("stale_after_seconds")
        if (not _finite_number(observed_at) or not _finite_number(stale_after)
                or stale_after <= 0):
            raise ValueError("capacity snapshot timestamps and freshness must be finite and positive")
        raw_workers = payload.get("workers")
        if not isinstance(raw_workers, dict) or not raw_workers:
            raise ValueError("capacity snapshot workers must be a non-empty object")
        observations: dict[str, CapacityObservation] = {}
        for worker_id, raw in raw_workers.items():
            if not isinstance(worker_id, str) or not _IDENTIFIER.fullmatch(worker_id):
                raise ValueError("capacity snapshot worker IDs must be stable identifiers")
            if not isinstance(raw, dict):
                raise ValueError(f"capacity for {worker_id} must be an object")
            fields = {"available", "free_slots", "quota_remaining", "cooldown_until"}
            extra = set(raw) - fields
            missing = {"available", "free_slots"} - set(raw)
            if extra or missing:
                details = []
                if missing:
                    details.append("missing " + ", ".join(sorted(missing)))
                if extra:
                    details.append("unknown " + ", ".join(sorted(extra)))
                raise ValueError(f"invalid capacity for {worker_id}: " + "; ".join(details))
            try:
                observations[worker_id] = CapacityObservation(
                    available=raw["available"],
                    free_slots=raw["free_slots"],
                    observed_at=observed_at,
                    stale_after_seconds=stale_after,
                    quota_remaining=raw.get("quota_remaining"),
                    cooldown_until=raw.get("cooldown_until"),
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid capacity for {worker_id}: {exc}") from exc
        if now is not None and not _finite_number(now):
            raise ValueError("now must be a finite numeric timestamp")
        current = time.time() if now is None else now
        if observed_at > current:
            raise ValueError("capacity snapshot cannot be observed in the future")
        return observations


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON configuration key: {key}")
        result[key] = value
    return result


def _finite_number(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))
