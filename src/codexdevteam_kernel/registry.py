"""Provider-neutral registry for configured worker identities."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from types import MappingProxyType
from typing import Mapping

from .identity import WorkerIdentity


REGISTRY_VERSION = 1
STRICT_REQUIRED_CAPABILITIES = frozenset({
    "control_protocol", "structured_edit_firewall", "task_worktree_isolation",
    "post_run_territory_gate",
})


@dataclass(frozen=True, slots=True)
class RolePolicy:
    """Configured model ordering and reasoning effort for one logical role."""

    role: str
    model_priority: tuple[str, ...] = ()
    reasoning_effort: str = "medium"

    def __post_init__(self) -> None:
        if not isinstance(self.role, str) or not self.role.strip():
            raise ValueError("role policy requires a role")
        if not all(isinstance(model, str) and model.strip() for model in self.model_priority):
            raise ValueError("model_priority entries must be non-empty configured model IDs")
        if len(set(self.model_priority)) != len(self.model_priority):
            raise ValueError("model_priority must not contain duplicates")
        if self.reasoning_effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
            raise ValueError("unsupported reasoning effort")


@dataclass(frozen=True, slots=True)
class WorkerDefinition:
    identity: WorkerIdentity
    control_mode: str = "legacy"
    strict_verification: Mapping[str, object] | None = None
    auth_ref: str | None = None
    machine_affinity: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.control_mode not in {"legacy", "strict"}:
            raise ValueError("control_mode must be 'legacy' or 'strict'")
        if self.control_mode == "strict":
            receipt = self.strict_verification
            if not isinstance(receipt, Mapping) or receipt.get("status") != "passed":
                raise ValueError("strict control requires a passed live-verification receipt")
            if receipt.get("runtime") != self.identity.runtime or receipt.get("model") != self.identity.model:
                raise ValueError("strict verification receipt must match configured runtime and model")
            verified_at = receipt.get("verified_at")
            evidence_ref = receipt.get("evidence_ref")
            if not isinstance(verified_at, str) or not isinstance(evidence_ref, str) or not evidence_ref.strip():
                raise ValueError("strict verification receipt requires verified_at and evidence_ref")
            try:
                normalized_time = verified_at[:-1] + "+00:00" if verified_at.endswith("Z") else verified_at
                parsed_time = datetime.fromisoformat(normalized_time)
            except ValueError as exc:
                raise ValueError("strict verification verified_at must be timezone-qualified ISO 8601") from exc
            if parsed_time.tzinfo is None or parsed_time.utcoffset() is None:
                raise ValueError("strict verification verified_at must include a timezone")
            if parsed_time.astimezone(timezone.utc).timestamp() > time.time() + 300:
                raise ValueError("strict verification verified_at cannot be in the future")
            capabilities = receipt.get("verified_capabilities")
            verified = ({value for value in capabilities if isinstance(value, str)}
                        if isinstance(capabilities, (list, tuple)) else set())
            if (not isinstance(capabilities, (list, tuple))
                    or not all(isinstance(value, str) and value for value in capabilities)
                    or len(set(capabilities)) != len(capabilities)
                    or not STRICT_REQUIRED_CAPABILITIES.issubset(verified)):
                missing = sorted(STRICT_REQUIRED_CAPABILITIES - verified)
                raise ValueError("strict verification receipt lacks required live capabilities: "
                                 + ", ".join(missing))
        if self.auth_ref is not None and not self.auth_ref.strip():
            raise ValueError("auth_ref must be null or a non-empty secret reference")
        if not all(isinstance(host, str) and host.strip() for host in self.machine_affinity):
            raise ValueError("machine_affinity entries must be non-empty strings")


@dataclass(frozen=True, slots=True)
class WorkerRegistry:
    defined: Mapping[str, WorkerDefinition]
    active: tuple[str, ...]
    head_candidate: str | None = None
    protocol_version: int = REGISTRY_VERSION
    role_policies: Mapping[str, RolePolicy] = field(default_factory=dict)
    capability_order: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.protocol_version != REGISTRY_VERSION:
            raise ValueError(f"unsupported worker registry version: {self.protocol_version}")
        if len(set(self.active)) != len(self.active):
            raise ValueError("active worker IDs must not contain duplicates")
        unknown = set(self.active) - set(self.defined)
        if unknown:
            raise ValueError(f"active workers are undefined: {', '.join(sorted(unknown))}")
        if self.head_candidate is not None and self.head_candidate not in self.defined:
            raise ValueError("head_candidate must refer to a defined worker")
        object.__setattr__(self, "defined", MappingProxyType(dict(self.defined)))
        policies = dict(self.role_policies)
        if any(key != value.role for key, value in policies.items()):
            raise ValueError("role policy keys must match their role")
        object.__setattr__(self, "role_policies", MappingProxyType(policies))
        if (not all(isinstance(item, str) and item.strip() for item in self.capability_order)
                or len(set(self.capability_order)) != len(self.capability_order)):
            raise ValueError("capability_order must contain unique non-empty capability names")

    @classmethod
    def from_dict(cls, data: dict) -> "WorkerRegistry":
        if not isinstance(data, dict):
            raise ValueError("worker registry must be an object")
        unknown = set(data) - {"protocol_version", "defined", "active", "head_candidate",
                               "role_policies", "capability_order"}
        if unknown:
            raise ValueError(f"unknown worker registry fields: {', '.join(sorted(unknown))}")
        version = data.get("protocol_version")
        if version != REGISTRY_VERSION:
            raise ValueError(f"unsupported worker registry version: {version}")
        raw_defined = data.get("defined")
        active = data.get("active")
        if not isinstance(raw_defined, dict) or not isinstance(active, list):
            raise ValueError("defined must be an object and active must be a list")
        definitions: dict[str, WorkerDefinition] = {}
        for unit_id, raw in raw_defined.items():
            if not isinstance(unit_id, str) or not unit_id.strip() or not isinstance(raw, dict):
                raise ValueError("each defined worker must have a non-empty ID and object config")
            try:
                allowed_fields = {"role", "capability_floor", "runtime", "model", "control_mode",
                                  "strict_verification", "auth_ref", "machine_affinity"}
                extra = set(raw) - allowed_fields
                if extra:
                    raise ValueError(f"unknown fields: {', '.join(sorted(extra))}")
                identity = WorkerIdentity(
                    unit_id=unit_id,
                    role=raw["role"],
                    capability_floor=raw["capability_floor"],
                    runtime=raw["runtime"],
                    model=raw["model"],
                )
                affinity = raw.get("machine_affinity", [])
                if not isinstance(affinity, list):
                    raise ValueError("machine_affinity must be a list")
                verification = raw.get("strict_verification")
                if verification is not None and not isinstance(verification, dict):
                    raise ValueError("strict_verification must be an object")
                definitions[unit_id] = WorkerDefinition(
                    identity=identity,
                    control_mode=raw.get("control_mode", "legacy"),
                    strict_verification=verification,
                    auth_ref=raw.get("auth_ref"),
                    machine_affinity=tuple(affinity),
                )
            except (KeyError, TypeError) as exc:
                raise ValueError(f"invalid worker '{unit_id}': missing or mistyped identity field") from exc
        raw_policies = data.get("role_policies", {})
        if not isinstance(raw_policies, dict):
            raise ValueError("role_policies must be an object")
        policies: dict[str, RolePolicy] = {}
        for role, raw in raw_policies.items():
            if not isinstance(role, str) or not role.strip() or not isinstance(raw, dict):
                raise ValueError("role policies require non-empty role names and object values")
            extra = set(raw) - {"model_priority", "reasoning_effort"}
            if extra:
                raise ValueError(f"unknown role policy fields: {', '.join(sorted(extra))}")
            models = raw.get("model_priority", [])
            if not isinstance(models, list):
                raise ValueError("model_priority must be a list")
            policies[role] = RolePolicy(role, tuple(models), raw.get("reasoning_effort", "medium"))
        if not all(isinstance(item, str) for item in active):
            raise ValueError("active worker IDs must be strings")
        capability_order = data.get("capability_order", [])
        if not isinstance(capability_order, list):
            raise ValueError("capability_order must be a list")
        return cls(definitions, tuple(active), data.get("head_candidate"), version, policies,
                   tuple(capability_order))

    def resolve(self, unit_id: str) -> WorkerDefinition:
        """Resolve only a defined identity; callers separately check active state."""
        try:
            return self.defined[unit_id]
        except KeyError as exc:
            raise ValueError(f"unknown worker identity: {unit_id}") from exc

    def active_workers(self) -> tuple[WorkerDefinition, ...]:
        return tuple(self.defined[unit_id] for unit_id in self.active)

    def candidate_head(self) -> WorkerDefinition | None:
        """Return configured candidate; this never acquires the project HEAD lease."""
        return self.resolve(self.head_candidate) if self.head_candidate else None

    def policy_for_role(self, role: str) -> RolePolicy | None:
        return self.role_policies.get(role)
