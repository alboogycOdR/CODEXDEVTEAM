"""Provider-neutral worker identity and policy dimensions."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkerIdentity:
    """Configured worker identity; role, capability, runtime, and model differ."""

    unit_id: str
    role: str
    capability_floor: str
    runtime: str
    model: str

    def __post_init__(self) -> None:
        for field_name in ("unit_id", "role", "capability_floor", "runtime", "model"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
