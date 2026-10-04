"""Fail-closed, metadata-only compatibility gates for mixed framework installs."""

from dataclasses import dataclass
from enum import StrEnum

from .control import CONTROL_VERSION
from .protocol import PROTOCOL_VERSION
from .registry import REGISTRY_VERSION


DEVDEPARTMENT_WAVE_E_SHA = "2d4202c9c70300f3f5cdf97a0b866c1a15b4c760"


class CompatibilityLevel(StrEnum):
    NATIVE = "native"
    SIDECAR_ONLY = "sidecar_only"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class CompatibilityDecision:
    level: CompatibilityLevel
    task_state_interoperable: bool
    activation_allowed: bool
    reasons: tuple[str, ...]


def assess_compatibility(metadata: dict) -> CompatibilityDecision:
    """Classify declared schema/version metadata without writing or activating."""
    if not isinstance(metadata, dict):
        raise ValueError("compatibility metadata must be an object")
    system = metadata.get("system")
    if system == "CODEXDEVTEAM":
        versions = (metadata.get("task_protocol_version"),
                    metadata.get("registry_version"), metadata.get("control_version"))
        expected = (PROTOCOL_VERSION, REGISTRY_VERSION, CONTROL_VERSION)
        if versions == expected:
            return CompatibilityDecision(CompatibilityLevel.NATIVE, True, False,
                                         ("native schemas match; activation is a separate operation",))
        return CompatibilityDecision(CompatibilityLevel.REFUSED, False, False,
                                     ("unknown or incompatible CODEXDEVTEAM schema versions",))
    if system == "DEVDEPARTMENT":
        exact = (metadata.get("revision") == DEVDEPARTMENT_WAVE_E_SHA
                 and metadata.get("sync_manifest_version") == 1
                 and metadata.get("sync_role") == "pack"
                 and metadata.get("plan_version") == "6.31"
                 and metadata.get("framework_version") is None)
        if exact:
            return CompatibilityDecision(
                CompatibilityLevel.SIDECAR_ONLY, False, False,
                ("recognized Wave E baseline; DEVDEPARTMENT remains incumbent",
                 "no shared task-state schema or handover protocol is declared"),
            )
        return CompatibilityDecision(CompatibilityLevel.REFUSED, False, False,
                                     ("unknown DEVDEPARTMENT revision or version metadata",))
    return CompatibilityDecision(CompatibilityLevel.REFUSED, False, False,
                                 ("unknown framework identity",))
