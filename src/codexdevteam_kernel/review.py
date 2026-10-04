"""Typed review evidence and mechanically enforceable maker/checker policy."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .gate import GateResult
from .identity import WorkerIdentity
from .protocol import TaskRecord
from .tasks import TaskState


_REQUIRED_GATE_CHECKS = {"sha", "clean_worktree", "territory", "secret_scan",
                         "build", "typecheck", "test_full", "reachability", "mutation",
                         "base_build", "base_typecheck", "base_test_full",
                         "base_reachability", "base_mutation", "baseline_analysis"}


def gate_artifact_findings(payload: dict) -> tuple[str, ...]:
    """Validate required gate evidence, including explicitly owned baseline failures."""
    findings: list[str] = []
    checks = payload.get("checks") if isinstance(payload, dict) else None
    if not isinstance(checks, dict) or not _REQUIRED_GATE_CHECKS.issubset(checks):
        return ("gate artifact is missing required mechanical checks",)
    for name, item in checks.items():
        if not isinstance(item, dict) or item.get("status") not in {"passed", "failed", "skipped"}:
            findings.append(f"gate check {name} has an invalid result")
    if any(not isinstance(checks[name], dict) or checks[name].get("status") != "passed"
           for name in ("sha", "clean_worktree", "territory", "secret_scan", "baseline_analysis")):
        findings.append("gate precondition or baseline analysis did not pass")
    inherited = payload.get("inherited_failures", [])
    owners = payload.get("failure_owner_ids", [])
    open_owners = payload.get("open_failure_owner_ids", [])
    if (not isinstance(inherited, list) or not isinstance(owners, list)
            or not isinstance(open_owners, list)
            or not set(open_owners).issubset(owners)):
        findings.append("gate failure ownership evidence is malformed")
        inherited, open_owners = [], []
    for name in ("build", "typecheck", "test_full", "reachability", "mutation"):
        item = checks.get(name)
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        if status == "passed":
            continue
        if status == "skipped" and item.get("summary") == "no command configured" and name in {
            "reachability", "mutation"
        }:
            continue
        if status == "failed" and name in inherited and open_owners:
            continue
        findings.append(f"configured gate check {name} did not pass or is not owned")
    for name in ("build", "typecheck", "test_full", "reachability", "mutation"):
        current = checks[name]
        baseline = checks[f"base_{name}"]
        if not isinstance(current, dict) or not isinstance(baseline, dict):
            continue
        if current.get("status") == "skipped" and current.get("summary") == "no command configured":
            if baseline.get("status") != "skipped" or baseline.get("summary") != "no command configured":
                findings.append(f"baseline check base_{name} does not match unconfigured task check")
        elif baseline.get("status") not in {"passed", "failed"}:
            findings.append(f"configured baseline check base_{name} has no completed result")
    if payload.get("new_failures"):
        findings.append("gate artifact reports newly introduced failures")
    return tuple(dict.fromkeys(findings))


@dataclass(frozen=True, slots=True)
class ReviewVerdict:
    task_id: str
    sha: str
    gate_fingerprint: str
    checker: WorkerIdentity
    decision: str
    rationale: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip() or not isinstance(self.sha, str) or len(self.sha) not in {40, 64}:
            raise ValueError("review requires task_id and full SHA")
        if self.decision not in {"approved", "changes_requested", "rejected"}:
            raise ValueError("invalid review decision")
        if not isinstance(self.rationale, str) or not self.rationale.strip() or not isinstance(self.gate_fingerprint, str) or not self.gate_fingerprint.strip():
            raise ValueError("review rationale and gate fingerprint are required")


def parse_review_verdict(output: str, checker: WorkerIdentity) -> ReviewVerdict:
    """Normalize one strict provider-neutral JSON review receipt.

    Checker identity is supplied by the invocation ledger, never trusted from
    model output. Unknown fields and prose/fenced JSON are rejected.
    """
    if not isinstance(output, str) or not output.strip():
        raise ValueError("review output must be non-empty JSON")
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ValueError("review output must be a single JSON object") from exc
    required = {"task_id", "sha", "gate_fingerprint", "decision", "rationale", "evidence_refs"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("review output must contain exactly the required review fields")
    refs = payload["evidence_refs"]
    if (not isinstance(refs, list) or not all(isinstance(ref, str) and ref.strip() for ref in refs)
            or len(set(refs)) != len(refs)):
        raise ValueError("evidence_refs must be a list of unique non-empty strings")
    return ReviewVerdict(
        task_id=payload["task_id"], sha=payload["sha"],
        gate_fingerprint=payload["gate_fingerprint"], checker=checker,
        decision=payload["decision"], rationale=payload["rationale"],
        evidence_refs=tuple(refs),
    )


def validate_review(task: TaskRecord, gate: GateResult, verdict: ReviewVerdict) -> tuple[str, ...]:
    """Validate attribution, exact gate binding, and independent checker identity."""
    findings: list[str] = []
    if task.state is not TaskState.NEEDS_REVIEW:
        findings.append("task is not awaiting review")
    if verdict.task_id != task.task_id or gate.task_id != task.task_id:
        findings.append("task identity does not match review and gate")
    if gate.status != "passed":
        findings.append("mechanical gate did not pass")
    if verdict.sha != gate.sha or verdict.gate_fingerprint != gate.fingerprint:
        findings.append("review is not bound to the passed gate SHA and fingerprint")
    maker = task.maker_identity
    if maker is None:
        findings.append("task has no immutable maker identity snapshot")
    else:
        if verdict.checker.unit_id == maker["unit_id"]:
            findings.append("maker and checker units must differ")
        if verdict.checker.runtime == maker["runtime"] and verdict.checker.model == maker["model"]:
            findings.append("maker and checker concrete runtime/model must differ")
    artifact = Path(gate.artifact_path)
    try:
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        if (payload.get("task_id") != gate.task_id or payload.get("sha") != gate.sha
                or payload.get("fingerprint") != gate.fingerprint
                or payload.get("status") != "passed"):
            findings.append("gate artifact does not match the supplied passed result")
        else:
            findings.extend(gate_artifact_findings(payload))
    except (OSError, ValueError, TypeError, AttributeError):
        findings.append("gate artifact is missing or invalid")
    return tuple(findings)


def review_digest(verdict: ReviewVerdict) -> str:
    body = {"task_id": verdict.task_id, "sha": verdict.sha,
            "gate_fingerprint": verdict.gate_fingerprint,
            "checker": {"unit_id": verdict.checker.unit_id, "runtime": verdict.checker.runtime,
                        "model": verdict.checker.model}, "decision": verdict.decision,
            "rationale": verdict.rationale, "evidence_refs": list(verdict.evidence_refs)}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
