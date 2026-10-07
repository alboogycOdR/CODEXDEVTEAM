"""Provider-neutral invocation outcome and cost aggregation."""

from dataclasses import dataclass
import math
from typing import Iterable, Mapping


@dataclass(frozen=True, slots=True)
class UsageRate:
    runtime: str
    model: str
    input_usd_per_million: float
    output_usd_per_million: float
    cached_input_usd_per_million: float | None = None
    effective_at: float = 0.0

    def __post_init__(self) -> None:
        if (not isinstance(self.runtime, str) or not self.runtime.strip()
                or not isinstance(self.model, str) or not self.model.strip()):
            raise ValueError("usage rate requires runtime and model")
        values = (self.input_usd_per_million, self.output_usd_per_million,
                  self.effective_at,
                  *(() if self.cached_input_usd_per_million is None
                    else (self.cached_input_usd_per_million,)))
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("usage rates and effective_at must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class InvocationSummary:
    role: str
    runtime: str
    model: str
    purpose: str
    invocations: int
    succeeded: int
    failed: int
    timed_out: int
    launch_failed: int
    duration_seconds: float
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    token_usage_complete: bool
    unmetered_token_invocations: int
    metered_invocations: int
    unmetered_invocations: int
    cost_usd: float | None
    review_approved: int
    review_changes_requested: int
    review_rejected: int
    first_pass_approved: int


@dataclass(frozen=True, slots=True)
class TierOutcome:
    runtime: str
    model: str
    reviewed_tasks: int
    first_pass_approved: int
    first_pass_rate: float | None
    maker_invocations: int
    total_cost_usd: float | None
    cost_per_first_pass_usd: float | None


@dataclass(frozen=True, slots=True)
class TierRentComparison:
    candidate: TierOutcome
    baseline: TierOutcome
    minimum_tasks: int
    minimum_first_pass_lift: float
    maximum_cost_per_approval_ratio: float
    pays_rent: bool | None
    reason: str


@dataclass(frozen=True, slots=True)
class PilotMetrics:
    reviewed_tasks: int
    first_pass_approved: int
    first_pass_rate: float | None
    review_sessions: int
    changes_requested: int
    gate_attempts: int
    gate_rejections: int
    gate_rejection_rate: float | None
    maker_invocations: int
    checker_invocations: int
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    token_usage_complete: bool
    unmetered_token_invocations: int
    known_spend_usd: float
    unmetered_invocations: int
    spend_complete: bool


def measure_pilot(store: object, *, rates: tuple[UsageRate, ...] = ()) -> PilotMetrics:
    """Measure a pilot from HEAD-verified review/gate events and invocation receipts."""
    required = ("events", "verified_review_events", "verified_gate_attempt_events")
    if any(not callable(getattr(store, name, None)) for name in required):
        raise ValueError("pilot metrics require a StateStore evidence source")
    events = store.events()
    reviews = sorted(store.verified_review_events(),
                     key=lambda event: (event.get("created_at", 0), event.get("event_id", "")))
    gates = store.verified_gate_attempt_events()
    by_task: dict[str, list[Mapping]] = {}
    sessions: set[str] = set()
    changes_requested = 0
    for event in reviews:
        payload = event.get("payload", {})
        task_id = payload.get("task_id")
        if not isinstance(task_id, str):
            continue
        by_task.setdefault(task_id, []).append(payload)
        checker_invocation = payload.get("checker_invocation_id")
        if isinstance(checker_invocation, str) and checker_invocation:
            sessions.add(checker_invocation)
        if payload.get("decision") == "changes_requested":
            changes_requested += 1
    first_pass = sum(bool(decisions) and decisions[0].get("decision") == "approved"
                     for decisions in by_task.values())
    failed_gates = sum(event.get("payload", {}).get("status") == "failed" for event in gates)
    gate_count = len(gates)
    summaries = summarize_invocations(events, rates=rates)
    maker_invocations = sum(row.invocations for row in summaries if row.purpose == "maker")
    checker_invocations = sum(row.invocations for row in summaries if row.purpose == "checker")
    all_invocations = sum(row.invocations for row in summaries)
    unmetered = sum(row.unmetered_invocations for row in summaries)
    token_usage_complete = all(row.token_usage_complete for row in summaries)
    unmetered_token_invocations = sum(row.unmetered_token_invocations for row in summaries)
    known_spend = sum(row.cost_usd or 0.0 for row in summaries)
    task_count = len(by_task)
    return PilotMetrics(
        reviewed_tasks=task_count,
        first_pass_approved=first_pass,
        first_pass_rate=first_pass / task_count if task_count else None,
        review_sessions=len(sessions),
        changes_requested=changes_requested,
        gate_attempts=gate_count,
        gate_rejections=failed_gates,
        gate_rejection_rate=failed_gates / gate_count if gate_count else None,
        maker_invocations=maker_invocations,
        checker_invocations=checker_invocations,
        input_tokens=(sum(row.input_tokens or 0 for row in summaries)
                      if token_usage_complete else None),
        output_tokens=(sum(row.output_tokens or 0 for row in summaries)
                       if token_usage_complete else None),
        cached_input_tokens=(sum(row.cached_input_tokens or 0 for row in summaries)
                             if token_usage_complete else None),
        token_usage_complete=token_usage_complete,
        unmetered_token_invocations=unmetered_token_invocations,
        known_spend_usd=round(known_spend, 12),
        unmetered_invocations=unmetered,
        spend_complete=all_invocations == 0 or unmetered == 0,
    )


def summarize_invocations(events: Iterable[Mapping], *,
                          rates: tuple[UsageRate, ...] = ()) -> tuple[InvocationSummary, ...]:
    """Group runtime receipts by role/provider/model/purpose with explicit unknown cost."""
    grouped: dict[tuple[str, str, str, str], dict] = {}
    ordered_rates = tuple(rates)
    all_events = list(events)
    invocation_payloads = []
    for event in all_events:
        payload = event.get("payload", event)
        if not isinstance(payload, Mapping) or payload.get("type") != "runtime.invoked":
            continue
        invocation_payloads.append(payload)
        key = (payload.get("role") or "unknown", payload.get("runtime", "unknown"),
               payload.get("model", "unknown"), payload.get("purpose", "unknown"))
        row = grouped.setdefault(key, {"invocations": 0, "succeeded": 0, "failed": 0,
                                       "timed_out": 0, "launch_failed": 0,
                                       "duration": 0.0, "input": 0, "input_seen": 0,
                                       "output": 0, "output_seen": 0,
                                       "cached": 0, "cached_seen": 0,
                                       "token_unmetered": 0,
                                       "metered": 0, "unmetered": 0, "cost": 0.0,
                                       "review_approved": 0, "review_changes_requested": 0,
                                       "review_rejected": 0, "first_pass_approved": 0})
        row["invocations"] += 1
        status = payload.get("status")
        if status in {"succeeded", "failed", "timed_out", "launch_failed"}:
            row[status] += 1
        duration = payload.get("duration_seconds")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration):
            row["duration"] += max(0.0, duration)
        token_fields_complete = True
        for name, counter in (("input_tokens", "input"), ("output_tokens", "output"),
                              ("cached_input_tokens", "cached")):
            value = payload.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                row[counter] += value
                row[counter + "_seen"] += 1
            else:
                token_fields_complete = False
        if not token_fields_complete:
            row["token_unmetered"] += 1
        cost = payload.get("reported_cost_usd")
        if cost is None:
            started = payload.get("started_at")
            applicable = [rate for rate in ordered_rates
                          if rate.runtime == key[1] and rate.model == key[2]
                          and isinstance(started, (int, float)) and started >= rate.effective_at]
            rate = max(applicable, key=lambda item: item.effective_at) if applicable else None
            inp, out, cached = (payload.get("input_tokens"), payload.get("output_tokens"),
                                payload.get("cached_input_tokens", 0))
            if (rate and isinstance(inp, int) and isinstance(out, int)
                    and isinstance(cached, int) and 0 <= cached <= inp):
                regular_input_rate = rate.input_usd_per_million
                cached_rate = (rate.cached_input_usd_per_million
                               if rate.cached_input_usd_per_million is not None
                               else regular_input_rate)
                cost = ((inp - cached) * regular_input_rate + cached * cached_rate
                        + out * rate.output_usd_per_million) / 1_000_000
        if (isinstance(cost, (int, float)) and not isinstance(cost, bool)
                and math.isfinite(cost) and cost >= 0):
            row["cost"] += float(cost)
            row["metered"] += 1
        else:
            row["unmetered"] += 1
    rework_count: dict[str, int] = {}
    review_events = sorted(
        ((event.get("created_at", index), event.get("payload", event))
         for index, event in enumerate(all_events)
         if isinstance(event.get("payload", event), Mapping)
         and event.get("payload", event).get("type") == "task.reviewed"),
        key=lambda item: item[0],
    )
    for _, payload in review_events:
        task_id = payload.get("task_id")
        decision = payload.get("decision")
        maker = payload.get("maker_identity")
        maker_invocation = next((item for item in invocation_payloads
                                 if item.get("task_id") == task_id
                                 and item.get("purpose") == "maker"
                                 and isinstance(maker, Mapping)
                                 and item.get("unit_id") == maker.get("unit_id")
                                 and item.get("runtime") == maker.get("runtime")
                                 and item.get("model") == maker.get("model")), None)
        if isinstance(maker, Mapping):
            maker_key = (maker_invocation.get("role", "unknown") if maker_invocation else "unknown",
                         maker.get("runtime", "unknown"), maker.get("model", "unknown"), "maker")
            _ensure_group(grouped, maker_key)
            _add_review_outcome(grouped[maker_key], decision, task_id, rework_count)
        checker_id = payload.get("checker_invocation_id")
        checker_invocation = next((item for item in invocation_payloads
                                   if item.get("invocation_id") == checker_id), None)
        checker = payload.get("checker")
        if isinstance(checker, Mapping):
            checker_key = (
                checker_invocation.get("role", "unknown") if checker_invocation else "unknown",
                checker.get("runtime", "unknown"), checker.get("model", "unknown"), "checker",
            )
            _ensure_group(grouped, checker_key)
            _add_review_outcome(grouped[checker_key], decision, task_id, rework_count)
    return tuple(InvocationSummary(
        role=key[0], runtime=key[1], model=key[2], purpose=key[3],
        invocations=row["invocations"], succeeded=row["succeeded"], failed=row["failed"],
        timed_out=row["timed_out"], launch_failed=row["launch_failed"],
        duration_seconds=row["duration"],
        input_tokens=row["input"] if row["input_seen"] else None,
        output_tokens=row["output"] if row["output_seen"] else None,
        cached_input_tokens=row["cached"] if row["cached_seen"] else None,
        token_usage_complete=(row["input_seen"] == row["invocations"]
                              and row["output_seen"] == row["invocations"]
                              and row["cached_seen"] == row["invocations"]),
        unmetered_token_invocations=row["token_unmetered"],
        metered_invocations=row["metered"], unmetered_invocations=row["unmetered"],
        cost_usd=round(row["cost"], 12) if row["metered"] else None,
        review_approved=row["review_approved"],
        review_changes_requested=row["review_changes_requested"],
        review_rejected=row["review_rejected"],
        first_pass_approved=row["first_pass_approved"],
    ) for key, row in sorted(grouped.items()))


def compare_tier_rent(events: Iterable[Mapping], *, candidate: tuple[str, str],
                      baseline: tuple[str, str], rates: tuple[UsageRate, ...] = (),
                      minimum_tasks: int = 5, minimum_first_pass_lift: float = 0.0,
                      maximum_cost_per_approval_ratio: float = 1.5) -> TierRentComparison:
    """Compare two maker tiers using first-review approvals and fully known cost.

    This is a measurement only: it never changes routing policy. Insufficient
    samples or unmetered invocations produce an indeterminate result.
    """
    for name, identity in (("candidate", candidate), ("baseline", baseline)):
        if (not isinstance(identity, tuple) or len(identity) != 2
                or not all(isinstance(value, str) and value.strip() for value in identity)):
            raise ValueError(f"{name} must be a (runtime, model) pair")
    if candidate == baseline:
        raise ValueError("candidate and baseline tiers must differ")
    if isinstance(minimum_tasks, bool) or not isinstance(minimum_tasks, int) or minimum_tasks < 1:
        raise ValueError("minimum_tasks must be a positive integer")
    if (isinstance(minimum_first_pass_lift, bool)
            or not isinstance(minimum_first_pass_lift, (int, float))
            or not math.isfinite(minimum_first_pass_lift) or minimum_first_pass_lift < 0
            or isinstance(maximum_cost_per_approval_ratio, bool)
            or not isinstance(maximum_cost_per_approval_ratio, (int, float))
            or not math.isfinite(maximum_cost_per_approval_ratio)
            or maximum_cost_per_approval_ratio <= 0):
        raise ValueError("tier rent thresholds must be finite and non-negative/positive")
    all_events = list(events)
    reviews = sorted(
        ((event.get("created_at", index), event.get("payload", event))
         for index, event in enumerate(all_events)
         if isinstance(event.get("payload", event), Mapping)
         and event.get("payload", event).get("type") == "task.reviewed"),
        key=lambda item: item[0],
    )
    reviewed: dict[tuple[str, str], dict[str, list[str]]] = {}
    for _, payload in reviews:
        maker = payload.get("maker_identity")
        task_id = payload.get("task_id")
        if (not isinstance(maker, Mapping) or not isinstance(task_id, str)
                or not isinstance(maker.get("runtime"), str)
                or not isinstance(maker.get("model"), str)):
            continue
        key = (maker["runtime"], maker["model"])
        reviewed.setdefault(key, {}).setdefault(task_id, []).append(payload.get("decision"))
    invocation_events = []
    for event in all_events:
        payload = event.get("payload", event)
        if isinstance(payload, Mapping) and payload.get("type") == "runtime.invoked":
            invocation_events.append((event, payload))

    def outcome_for(identity: tuple[str, str]) -> TierOutcome:
        tasks = reviewed.get(identity, {})
        first_pass = sum(bool(decisions) and decisions[0] == "approved"
                         for decisions in tasks.values())
        scoped = [event for event, payload in invocation_events
                  if payload.get("purpose") == "maker"
                  and payload.get("runtime") == identity[0]
                  and payload.get("model") == identity[1]
                  and payload.get("task_id") in tasks]
        summaries = summarize_invocations(scoped, rates=rates)
        invocations = sum(row.invocations for row in summaries)
        metered = sum(row.metered_invocations for row in summaries)
        cost = (sum(row.cost_usd or 0.0 for row in summaries)
                if invocations > 0 and invocations == metered else None)
        rate = first_pass / len(tasks) if tasks else None
        per_approval = cost / first_pass if cost is not None and first_pass else None
        return TierOutcome(identity[0], identity[1], len(tasks), first_pass,
                           rate, invocations, cost, per_approval)

    candidate_result = outcome_for(candidate)
    baseline_result = outcome_for(baseline)
    if (candidate_result.reviewed_tasks < minimum_tasks
            or baseline_result.reviewed_tasks < minimum_tasks):
        pays, reason = None, "insufficient reviewed-task sample for one or both tiers"
    elif candidate_result.total_cost_usd is None or baseline_result.total_cost_usd is None:
        pays, reason = None, "one or both tiers have unmetered maker invocations"
    elif (candidate_result.cost_per_first_pass_usd is None
          or baseline_result.cost_per_first_pass_usd is None):
        pays, reason = None, "one or both tiers have no first-pass approvals to price"
    else:
        lift = candidate_result.first_pass_rate - baseline_result.first_pass_rate
        cost_ratio = (candidate_result.cost_per_first_pass_usd
                      / baseline_result.cost_per_first_pass_usd
                      if baseline_result.cost_per_first_pass_usd > 0
                      else (0.0 if candidate_result.cost_per_first_pass_usd == 0 else math.inf))
        pays = (lift >= minimum_first_pass_lift
                and cost_ratio <= maximum_cost_per_approval_ratio)
        reason = ("meets configured first-pass lift and cost-per-approval thresholds"
                  if pays else "misses configured first-pass lift or cost-per-approval threshold")
    return TierRentComparison(candidate_result, baseline_result, minimum_tasks,
                              float(minimum_first_pass_lift),
                              float(maximum_cost_per_approval_ratio), pays, reason)


def _ensure_group(grouped: dict, key: tuple[str, str, str, str]) -> None:
    grouped.setdefault(key, {"invocations": 0, "succeeded": 0, "failed": 0,
                             "timed_out": 0, "launch_failed": 0,
                             "duration": 0.0, "input": 0, "input_seen": 0,
                             "output": 0, "output_seen": 0, "cached": 0,
                             "cached_seen": 0, "token_unmetered": 0,
                             "metered": 0, "unmetered": 0,
                             "cost": 0.0, "review_approved": 0,
                             "review_changes_requested": 0, "review_rejected": 0,
                             "first_pass_approved": 0})


def _add_review_outcome(row: dict, decision: object, task_id: object,
                        rework_count: dict[str, int]) -> None:
    if decision == "approved":
        row["review_approved"] += 1
        if isinstance(task_id, str) and rework_count.get(task_id, 0) == 0:
            row["first_pass_approved"] += 1
    elif decision == "changes_requested":
        row["review_changes_requested"] += 1
        if isinstance(task_id, str):
            rework_count[task_id] = rework_count.get(task_id, 0) + 1
    elif decision == "rejected":
        row["review_rejected"] += 1
