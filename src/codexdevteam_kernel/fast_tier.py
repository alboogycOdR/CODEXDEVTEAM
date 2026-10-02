"""Bounded schema-validated fast-tier mechanical jobs."""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import re
import time
from typing import Mapping
from pathlib import Path

from .registry import WorkerRegistry
from .runtime import InvocationRequest, InvocationResult, request_for_worker
from .secrets import find_secrets
from .state import HeadLease, StateStore


_RUN_KINDS = frozenset({"ok", "capacity", "quota", "auth", "crash", "timeout"})


@dataclass(frozen=True, slots=True)
class FastTierResult:
    job_id: str
    worker_id: str
    classification: Mapping[str, str] | None
    invocations: tuple[InvocationResult, ...]
    escalated: bool
    reason: str | None = None


class FastTierRunner:
    """Run the fixed run-log classifier contract with one retry and one escalation."""

    MAX_LOG_CHARS = 24_000

    def __init__(self, store: StateStore, registry: WorkerRegistry):
        self.store = store
        self.registry = registry

    def classify_run_log(self, lease: HeadLease, *, job_id: str, worker_id: str,
                         run_log: str, adapters: Mapping[str, object],
                         working_directory: str | Path,
                         timeout_seconds: float = 120.0,
                         now: float | None = None) -> FastTierResult:
        if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", job_id):
            raise ValueError("job_id is invalid")
        if (not isinstance(run_log, str) or not run_log.strip()
                or len(run_log) > self.MAX_LOG_CHARS or find_secrets(run_log)):
            raise ValueError("run log must be non-empty, bounded, and free of detected secrets")
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("timeout_seconds must be positive")
        cwd = Path(working_directory).resolve(strict=True)
        if not cwd.is_dir():
            raise ValueError("working_directory must be an existing directory")
        if worker_id not in self.registry.active:
            raise ValueError("fast-tier worker must be active")
        worker = self.registry.resolve(worker_id)
        if worker.identity.role != "fast":
            raise ValueError("fast-tier jobs require a worker configured with role 'fast'")
        adapter = adapters.get(worker.identity.runtime)
        if adapter is None or not callable(getattr(adapter, "invoke", None)):
            raise ValueError(f"no invocation adapter for runtime {worker.identity.runtime}")
        prompt = (
            "Classify the run log. Return exactly one JSON object with keys "
            '"kind", "evidence_line", and optional "reset_at". '
            f"kind must be one of {', '.join(sorted(_RUN_KINDS))}. "
            "evidence_line must be copied exactly from the supplied log. "
            "Do not return markdown or prose.\n\nRUN LOG\n" + run_log
        )
        invocations: list[InvocationResult] = []
        classification = None
        reason = "invalid fast-tier output"
        for attempt in (1, 2):
            invocation_id = f"{job_id}:fast:{attempt}"
            request = request_for_worker(
                self.registry, worker_id, invocation_id=invocation_id, task_id=None,
                purpose="triage", prompt=prompt, working_directory=str(cwd),
                timeout_seconds=float(timeout_seconds), writable=False,
            )
            started = time.time()
            try:
                result = adapter.invoke(request)
            except Exception:
                finished = time.time()
                error = "fast-tier adapter failed"
                result = InvocationResult(
                    invocation_id, None, "triage", worker.identity.unit_id,
                    worker.identity.runtime, worker.identity.model, "launch_failed", None,
                    max(0.0, finished - started), "", error, False,
                    hashlib.sha256(error.encode("utf-8")).hexdigest(), started, finished,
                    role=worker.identity.role,
                )
            if (result.invocation_id != invocation_id or result.task_id is not None
                    or result.purpose != "triage" or result.unit_id != worker.identity.unit_id
                    or result.runtime != worker.identity.runtime or result.model != worker.identity.model):
                raise ValueError("runtime adapter returned a mismatched fast-tier receipt")
            if result.role is not None and result.role != worker.identity.role:
                raise ValueError("runtime adapter returned a mismatched fast-tier role")
            if result.role is None:
                from dataclasses import replace
                result = replace(result, role=worker.identity.role)
            self.store.record_invocation(lease, result, now=now)
            invocations.append(result)
            if result.status != "succeeded" or result.exit_code != 0:
                reason = "fast-tier runtime did not complete successfully"
                break
            classification = _parse_run_classification(result.stdout, run_log)
            if classification is not None:
                reason = ""
                break
        escalated = classification is None
        if not escalated:
            self.store.record_event(
                lease, f"tier-classified:{job_id}",
                {"type": "tier.classified", "job_id": job_id, "worker_id": worker_id,
                 "job": "classify_run_log", "classification": classification}, now=now,
            )
        else:
            self.store.record_event(
                lease, f"tier-escalate:{job_id}",
                {"type": "tier.escalated", "job_id": job_id, "worker_id": worker_id,
                 "job": "classify_run_log", "attempts": len(invocations), "reason": reason},
                now=now,
            )
        return FastTierResult(job_id, worker_id, classification,
                              tuple(invocations), escalated, reason or None)


def _parse_run_classification(output: str, run_log: str) -> dict[str, str] | None:
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or set(payload) not in (
        {"kind", "evidence_line"}, {"kind", "evidence_line", "reset_at"}
    ):
        return None
    kind, line = payload.get("kind"), payload.get("evidence_line")
    if (not isinstance(kind, str) or kind not in _RUN_KINDS
            or not isinstance(line, str) or not line.strip()
            or len(line) > 1000
            or line not in run_log.splitlines() or find_secrets(line)):
        return None
    result = {"kind": kind, "evidence_line": line}
    if "reset_at" in payload:
        reset_at = payload["reset_at"]
        if not isinstance(reset_at, str):
            return None
        try:
            parsed = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        result["reset_at"] = reset_at
    return result
