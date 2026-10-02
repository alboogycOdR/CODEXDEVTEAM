"""Pure stale/stagnation policy helpers; callers supply all observations."""

from dataclasses import dataclass
from datetime import datetime, timezone


DEFAULT_STAGNATION_POLICY = {
    "no_progress_ticks": 3,
    "denial_ticks": 2,
    "max_stagnation_resets": 2,
}


@dataclass(frozen=True, slots=True)
class StagnationSample:
    changed: bool
    denials: int

    def __post_init__(self) -> None:
        if not isinstance(self.changed, bool):
            raise ValueError("changed must be boolean")
        if not isinstance(self.denials, int) or self.denials < 0:
            raise ValueError("denials must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class HealthSignal:
    stale: bool
    age_seconds: float
    reason: str


def update_streak(prior_streak: int, sample: StagnationSample) -> int:
    if not isinstance(prior_streak, int) or prior_streak < 0:
        raise ValueError("prior_streak must be a non-negative integer")
    return 0 if sample.changed else prior_streak + 1


def is_stagnant(streak: int, sample: StagnationSample,
                policy: dict | None = None) -> bool:
    if sample.changed:
        return False
    config = {**DEFAULT_STAGNATION_POLICY, **(policy or {})}
    no_progress = config["no_progress_ticks"]
    denial_ticks = config["denial_ticks"]
    if (not isinstance(no_progress, int) or no_progress < 1
            or not isinstance(denial_ticks, int) or denial_ticks < 1):
        raise ValueError("stagnation thresholds must be positive integers")
    return streak >= no_progress or sample.denials >= denial_ticks


def remedial_kind(prior_resets: int, policy: dict | None = None) -> str:
    config = {**DEFAULT_STAGNATION_POLICY, **(policy or {})}
    maximum = config["max_stagnation_resets"]
    if not isinstance(maximum, int) or maximum < 0:
        raise ValueError("max_stagnation_resets must be a non-negative integer")
    if not isinstance(prior_resets, int) or prior_resets < 0:
        raise ValueError("prior_resets must be a non-negative integer")
    return "escalate" if prior_resets >= maximum else "redispatch"


def stale_signal(last_heartbeat: datetime | None, *, now: datetime,
                 stale_after_seconds: float) -> HealthSignal:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if stale_after_seconds < 0:
        raise ValueError("stale_after_seconds must be non-negative")
    if last_heartbeat is None:
        return HealthSignal(True, float("inf"), "missing heartbeat")
    if last_heartbeat.tzinfo is None:
        raise ValueError("last_heartbeat must be timezone-aware")
    age = (now.astimezone(timezone.utc) - last_heartbeat.astimezone(timezone.utc)).total_seconds()
    if age < 0:
        return HealthSignal(False, age, "heartbeat is in the future")
    return HealthSignal(age >= stale_after_seconds, age,
                        "heartbeat is stale" if age >= stale_after_seconds else "heartbeat is fresh")
