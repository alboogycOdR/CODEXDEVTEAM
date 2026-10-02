"""Deterministic fact miners over HEAD-owned mechanical evidence."""

import hashlib
import re
from typing import Protocol

from .memory import EvidenceFact, EvidenceMemory
from .territory import decide_write, normalize_repo_path


class EventSource(Protocol):
    def verified_test_run_events(self) -> list[dict]: ...
    def verified_territory_conflict_events(self) -> list[dict]: ...
    def verified_review_events(self) -> list[dict]: ...
    def verified_gate_attempt_events(self) -> list[dict]: ...


def mine_passing_test_facts(memory: EvidenceMemory, source: EventSource, *,
                            project: str) -> tuple[EvidenceFact, ...]:
    """Mine one cited test-command fact per valid HEAD-registered passing run."""
    if not isinstance(project, str) or not project.strip():
        raise ValueError("project identity is required")
    facts: list[EvidenceFact] = []
    for event in source.verified_test_run_events():
        if not isinstance(event, dict):
            continue
        payload = event.get("payload")
        event_id = event.get("event_id")
        if (not isinstance(payload, dict) or payload.get("type") != "test_run.registered"
                or not isinstance(event_id, str) or not event_id.strip()):
            continue
        test_name, task_id, sha, evidence_ref = (
            payload.get("test_name"), payload.get("task_id"),
            payload.get("sha"), payload.get("evidence_ref"),
        )
        if (not isinstance(test_name, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", test_name)
                or not isinstance(task_id, str) or not re.fullmatch(r"TASK-[A-Z0-9][A-Z0-9-]*", task_id)
                or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40,64}", sha)
                or not isinstance(evidence_ref, str)
                or not evidence_ref.startswith(f"testrun:{sha}:{test_name}:")):
            continue
        fact_id = "fact-testcmd-" + hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:24]
        evidence = (f"state-event:{event_id}", evidence_ref, f"commit:{sha}")
        statement = (f"Configured check {test_name} has a HEAD-registered passing run "
                     f"for {task_id} at this revision.")
        prior = memory.get_fact(fact_id)
        if prior is not None:
            if (prior.kind, prior.scope, prior.subject, prior.statement, prior.evidence,
                    prior.origin_project) != (
                    "test_command", "project", test_name, statement, evidence, project):
                raise ValueError("deterministic test fact ID conflicts with stored fact")
            facts.append(prior)
            continue
        created_at = event.get("created_at")
        facts.append(memory.add_fact(
            fact_id=fact_id, kind="test_command", scope="project", subject=test_name,
            statement=statement, evidence=evidence, origin_project=project,
            now=created_at,
        ))
    return tuple(facts)


def mine_hot_file_facts(memory: EvidenceMemory, source: EventSource, *,
                        project: str, now: float, window_seconds: float = 30 * 86400,
                        threshold: int = 2) -> tuple[EvidenceFact, ...]:
    """Mine exact-path hot-file guidance after repeated verified territory denials."""
    if not isinstance(project, str) or not project.strip():
        raise ValueError("project identity is required")
    if now < 0 or window_seconds <= 0 or threshold < 2:
        raise ValueError("a positive window and threshold of at least two are required")
    sightings: dict[str, list[dict]] = {}
    for event in source.verified_territory_conflict_events():
        if not isinstance(event, dict):
            continue
        event_id = event.get("event_id")
        created_at = event.get("created_at")
        if (not isinstance(event_id, str) or not event_id.strip()
                or not isinstance(created_at, (int, float))
                or isinstance(created_at, bool)
                or created_at < now - window_seconds or created_at > now):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "territory.conflict":
            continue
        for conflict in payload.get("conflicts", []):
            if not isinstance(conflict, dict):
                continue
            subject = conflict.get("subject_path")
            if isinstance(subject, str) and subject.strip():
                sightings.setdefault(subject, []).append(event)

    facts = []
    for subject, events in sorted(sightings.items()):
        unique = {event["event_id"]: event for event in events}
        if len(unique) < threshold:
            continue
        chosen = sorted(unique.values(), key=lambda event: (event["created_at"], event["event_id"]))[:threshold]
        ids = [event["event_id"] for event in chosen]
        digest = hashlib.sha256((project + "\0" + subject + "\0" + "\0".join(ids)).encode("utf-8")).hexdigest()[:24]
        fact_id = "fact-hotfile-" + digest
        evidence = tuple(f"state-event:{event_id}" for event_id in ids)
        statement = f"Coordinate edits to {subject}; repeated task assignments have conflicted there."
        prior = memory.get_fact(fact_id)
        if prior is not None:
            if (prior.kind, prior.scope, prior.subject, prior.statement, prior.evidence,
                    prior.origin_project) != ("hot_file", "project", subject, statement, evidence, project):
                raise ValueError("deterministic hot-file fact ID conflicts with stored fact")
            facts.append(prior)
            continue
        facts.append(memory.add_fact(
            fact_id=fact_id, kind="hot_file", scope="project", subject=subject,
            statement=statement, evidence=evidence, origin_project=project,
            now=max(event["created_at"] for event in chosen),
        ))
    return tuple(facts)


def mine_review_catch_facts(memory: EvidenceMemory, source: EventSource, *,
                            project: str, now: float, window_seconds: float = 30 * 86400,
                            threshold: int = 2) -> tuple[EvidenceFact, ...]:
    """Mine cited paths from repeated, verified changes-requested reviews."""
    if not isinstance(project, str) or not project.strip():
        raise ValueError("project identity is required")
    if now < 0 or window_seconds <= 0 or threshold < 2:
        raise ValueError("a positive window and threshold of at least two are required")
    sightings: dict[str, list[dict]] = {}
    for event in source.verified_review_events():
        if not isinstance(event, dict) or not isinstance(event.get("event_id"), str):
            continue
        created_at = event.get("created_at")
        if (not isinstance(created_at, (int, float)) or created_at < now - window_seconds
                or created_at > now):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "task.reviewed" \
                or payload.get("decision") != "changes_requested":
            continue
        owned_paths = event.get("task_owned_paths")
        refs = payload.get("evidence_refs")
        if (not isinstance(owned_paths, list) or not all(isinstance(path, str) for path in owned_paths)
                or not isinstance(refs, list)):
            continue
        for reference in refs:
            if not isinstance(reference, str) or not reference.startswith("file:"):
                continue
            try:
                path = normalize_repo_path(reference[len("file:"):])
            except ValueError:
                continue
            if not decide_write(path, owned_paths).allowed:
                continue
            sightings.setdefault(path, []).append(event)

    facts = []
    for path, events in sorted(sightings.items()):
        unique = {event["event_id"]: event for event in events}
        if len(unique) < threshold:
            continue
        chosen = sorted(unique.values(), key=lambda event: (event["created_at"], event["event_id"]))[:threshold]
        ids = [event["event_id"] for event in chosen]
        digest = hashlib.sha256((project + "\0" + path + "\0" + "\0".join(ids)).encode()).hexdigest()[:24]
        fact_id = "fact-reviewcatch-" + digest
        evidence = tuple(f"state-event:{event_id}" for event_id in ids)
        statement = f"Include explicit review coverage for {path}; it was cited in repeated change requests."
        prior = memory.get_fact(fact_id)
        expected = ("review_catch", "project", path, statement, evidence, project)
        if prior is not None:
            if (prior.kind, prior.scope, prior.subject, prior.statement, prior.evidence,
                    prior.origin_project) != expected:
                raise ValueError("deterministic review fact ID conflicts with stored fact")
            facts.append(prior)
            continue
        facts.append(memory.add_fact(
            fact_id=fact_id, kind="review_catch", scope="project", subject=path,
            statement=statement, evidence=evidence, origin_project=project,
            now=max(event["created_at"] for event in chosen),
        ))
    return tuple(facts)


def mine_gate_history_facts(memory: EvidenceMemory, source: EventSource, *,
                            project: str, now: float, window_seconds: float = 30 * 86400,
                            threshold: int = 2) -> tuple[EvidenceFact, ...]:
    """Mine repeated new mechanical-gate failures from intact HEAD-recorded artifacts."""
    if not isinstance(project, str) or not project.strip():
        raise ValueError("project identity is required")
    if now < 0 or window_seconds <= 0 or threshold < 2:
        raise ValueError("a positive window and threshold of at least two are required")
    sightings: dict[str, dict[tuple[str, str, str], dict]] = {}
    for event in source.verified_gate_attempt_events():
        if not isinstance(event, dict) or not isinstance(event.get("event_id"), str):
            continue
        created_at = event.get("created_at")
        if (not isinstance(created_at, (int, float)) or created_at < now - window_seconds
                or created_at > now):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "gate.attempted":
            continue
        task_id, sha, fingerprint = payload.get("task_id"), payload.get("sha"), payload.get("fingerprint")
        new_failures, checks = payload.get("new_failures"), payload.get("checks")
        if (not isinstance(task_id, str) or not isinstance(sha, str)
                or not isinstance(fingerprint, str) or not isinstance(new_failures, list)
                or not isinstance(checks, dict)):
            continue
        for check in new_failures:
            if isinstance(check, str) and checks.get(check) == "failed":
                sightings.setdefault(check, {}).setdefault((task_id, sha, fingerprint), event)

    facts = []
    for check, unique in sorted(sightings.items()):
        if len(unique) < threshold:
            continue
        chosen = sorted(unique.values(), key=lambda event: (event["created_at"], event["event_id"]))[:threshold]
        ids = [event["event_id"] for event in chosen]
        digest = hashlib.sha256((project + "\0" + check + "\0" + "\0".join(ids)).encode()).hexdigest()[:24]
        fact_id = "fact-gatecheck-" + digest
        evidence = tuple(f"state-event:{event_id}" for event_id in ids)
        statement = f"Resolve new {check} gate failures before review; this check failed repeatedly."
        expected = ("spec_gotcha", "project", "gate-check:" + check, statement, evidence, project)
        prior = memory.get_fact(fact_id)
        if prior is not None:
            if (prior.kind, prior.scope, prior.subject, prior.statement, prior.evidence,
                    prior.origin_project) != expected:
                raise ValueError("deterministic gate fact ID conflicts with stored fact")
            facts.append(prior)
            continue
        facts.append(memory.add_fact(
            fact_id=fact_id, kind="spec_gotcha", scope="project", subject="gate-check:" + check,
            statement=statement, evidence=evidence, origin_project=project,
            now=max(event["created_at"] for event in chosen),
        ))
    return tuple(facts)
