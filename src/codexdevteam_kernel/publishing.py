"""Provider-neutral publication decisions; this module never runs Git effects."""

from dataclasses import dataclass
import math
from typing import Mapping


_POLICIES = {"every", "batch", "merge_only"}
_EVENTS = {"bookkeeping", "merge", "park"}


@dataclass(frozen=True, slots=True)
class PublicationDecision:
    """Whether a caller may publish now and the durable batch-window update."""

    publish: bool
    reason: str
    next_window_start: float | None


def decide_publication(config: Mapping[str, object], state: Mapping[str, object], *,
                       event: str, now: float,
                       only_if_configured: bool = False) -> PublicationDecision:
    """Reduce configured push policy and durable window state to one decision.

    The caller persists ``next_window_start`` and performs any remote operation.
    This function deliberately has no Git, filesystem, or network side effects.
    Missing policy preserves the historical ``every`` schedule, but publication
    itself remains disabled unless ``enabled`` is explicitly true. Callers can
    also request legacy local-only behavior with ``only_if_configured``.
    """
    if event not in _EVENTS:
        raise ValueError(f"invalid publication event: {event}")
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now) or now < 0:
        raise ValueError("now must be a finite non-negative timestamp")
    if not isinstance(config, Mapping) or not isinstance(state, Mapping):
        raise ValueError("publication config and state must be mappings")

    configured = "push_policy" in config
    policy = config.get("push_policy", "every")
    if not isinstance(policy, str) or policy not in _POLICIES:
        raise ValueError(f"invalid git.push_policy: {policy!r}")
    batch_minutes = config.get("push_batch_minutes", 30)
    if (isinstance(batch_minutes, bool) or not isinstance(batch_minutes, (int, float))
            or not math.isfinite(batch_minutes) or batch_minutes <= 0):
        raise ValueError("git.push_batch_minutes must be a finite positive number")
    enabled = config.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("git.enabled must be boolean")
    if only_if_configured and not configured:
        return PublicationDecision(False, "policy not configured; preserve local-only behavior",
                                   _window_start(state))
    if not enabled:
        return PublicationDecision(False, "publication is disabled by configuration",
                                   _window_start(state))
    if not configured:
        raise ValueError("git.push_policy must be explicit when publication is enabled")

    window_start = _window_start(state)
    if event == "bookkeeping" and policy == "merge_only":
        return PublicationDecision(False, "deferred until merge or park", window_start)
    if event == "bookkeeping" and policy == "batch":
        if window_start is None or now < window_start:
            return PublicationDecision(False, "deferred until batch boundary", float(now))
        if now - window_start < float(batch_minutes) * 60:
            return PublicationDecision(False, "deferred until batch boundary", window_start)
    return PublicationDecision(True, "publication boundary reached", float(now))


def _window_start(state: Mapping[str, object]) -> float | None:
    value = state.get("window_start")
    if value is None:
        return None
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError("push window_start must be a finite non-negative timestamp")
    return float(value)
