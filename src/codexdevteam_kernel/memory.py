"""Evidence-cited project facts with bounded retrieval and measured lifecycle."""

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from contextlib import closing
from typing import Iterable

from .protocol import TaskRecord, _territories_intersect
from .secrets import find_secrets
from .territory import normalize_repo_path


FACT_KINDS = frozenset({"hot_file", "test_command", "carve_rule", "unit_tendency",
                        "env_quirk", "spec_gotcha", "review_catch"})
ACTIVE = "active"
PROBATION = "probation"
RETIRED = "retired"
SEED_ALPHA = 1.5
SEED_BETA = 1.0
PROBATION_MIN_INJECTIONS = 5
PROBATION_THRESHOLD = 0.35
REACTIVATION_THRESHOLD = 0.6
RETIRE_AFTER_SECONDS = 30 * 24 * 60 * 60
RECENCY_HALF_LIFE_SECONDS = 90 * 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class EvidenceFact:
    fact_id: str
    kind: str
    scope: str
    subject: str
    statement: str
    evidence: tuple[str, ...]
    created_at: float
    last_seen_at: float
    injections: int
    wins: int
    losses: int
    confidence: float
    status: str
    origin_project: str
    probation_at: float | None = None


@dataclass(frozen=True, slots=True)
class FactInjection:
    event_id: str
    task_id: str
    facts: tuple[EvidenceFact, ...]
    estimated_tokens: int


class EvidenceMemory:
    """Local SQLite evidence store. It never writes framework or constitutional files."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS facts (
                    fact_id TEXT PRIMARY KEY, kind TEXT NOT NULL, scope TEXT NOT NULL,
                    subject TEXT NOT NULL, statement TEXT NOT NULL, evidence_json TEXT NOT NULL,
                    created_at REAL NOT NULL, last_seen_at REAL NOT NULL,
                    injections INTEGER NOT NULL, wins INTEGER NOT NULL, losses INTEGER NOT NULL,
                    confidence REAL NOT NULL, status TEXT NOT NULL, origin_project TEXT NOT NULL,
                    probation_at REAL
                );
                CREATE TABLE IF NOT EXISTS memory_events (
                    event_id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fact_injections (
                    event_id TEXT NOT NULL, task_id TEXT NOT NULL, fact_id TEXT NOT NULL,
                    outcome TEXT, PRIMARY KEY(event_id, fact_id),
                    FOREIGN KEY(fact_id) REFERENCES facts(fact_id)
                );
                CREATE TABLE IF NOT EXISTS injection_reviews (
                    injection_event_id TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
                    outcome TEXT NOT NULL
                );
            """)

    def add_fact(self, *, fact_id: str, kind: str, scope: str, subject: str,
                 statement: str, evidence: Iterable[str], origin_project: str,
                 now: float | None = None) -> EvidenceFact:
        """Create a fact only with evidence citations and safe, non-secret content."""
        current = _clock(now)
        citations = tuple(evidence)
        if (not isinstance(fact_id, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", fact_id)):
            raise ValueError("fact_id must be a non-empty safe identifier")
        if not isinstance(kind, str) or kind not in FACT_KINDS:
            raise ValueError("unsupported fact kind")
        if not isinstance(scope, str) or not (scope == "project" or scope.startswith("stack:")):
            raise ValueError("scope must be project or stack:<tag>")
        if scope.startswith("stack:") and not scope[6:].strip():
            raise ValueError("stack scope requires a tag")
        for name, value in (("subject", subject), ("statement", statement),
                            ("origin_project", origin_project)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if not citations or not all(isinstance(ref, str) and ref.strip() for ref in citations):
            raise ValueError("evidence citations are required")
        if len(set(citations)) != len(citations):
            raise ValueError("evidence citations must be unique")
        if (find_secrets(fact_id) or find_secrets(statement)
                or any(find_secrets(ref) for ref in citations)):
            raise ValueError("secret-like content cannot be stored as a fact")
        payload = {"fact_id": fact_id, "kind": kind, "scope": scope, "subject": subject,
                   "statement": statement, "evidence": citations,
                   "origin_project": origin_project}
        with closing(self._connect()) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?, NULL)",
                           (fact_id, kind, scope, subject, statement,
                            json.dumps(citations, separators=(",", ":")), current, current,
                            _confidence(0, 0), ACTIVE, origin_project))
                self._event(db, f"fact-created:{fact_id}", "created", payload, current)
                db.commit()
            except sqlite3.IntegrityError as exc:
                db.rollback()
                raise ValueError("fact_id already exists") from exc
            except Exception:
                db.rollback()
                raise
        return self.get_fact(fact_id)  # type: ignore[return-value]

    def get_fact(self, fact_id: str) -> EvidenceFact | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
        return _fact(row) if row else None

    def list_facts(self, *, status: str | None = None) -> tuple[EvidenceFact, ...]:
        if status is not None and status not in {ACTIVE, PROBATION, RETIRED}:
            raise ValueError("invalid fact status")
        with closing(self._connect()) as db:
            rows = (db.execute("SELECT * FROM facts ORDER BY fact_id").fetchall() if status is None
                    else db.execute("SELECT * FROM facts WHERE status=? ORDER BY fact_id",
                                    (status,)).fetchall())
        return tuple(_fact(row) for row in rows)

    def retrieve_for_task(self, *, event_id: str, task_id: str, project: str,
                          owned_paths: tuple[str, ...], subjects: tuple[str, ...] = (),
                          stack_tags: tuple[str, ...] = (), limit: int = 5,
                          token_budget: int = 600, now: float | None = None) -> FactInjection:
        """Select active scoped facts by confidence×recency and ledger the injection."""
        current = _clock(now)
        if any(not isinstance(value, str) or not value.strip()
               for value in (event_id, task_id, project)):
            raise ValueError("event_id, task_id, and project are required")
        if (not owned_paths or isinstance(limit, bool) or not isinstance(limit, int)
                or limit < 0 or isinstance(token_budget, bool)
                or not isinstance(token_budget, int) or token_budget < 0):
            raise ValueError("owned paths are required; limits must be non-negative")
        if not all(isinstance(path, str) and path.strip() for path in owned_paths):
            raise ValueError("owned paths must be non-empty strings")
        if (not isinstance(subjects, tuple)
                or not all(isinstance(item, str) and item.strip() for item in subjects)
                or not isinstance(stack_tags, tuple)
                or not all(isinstance(item, str) and item.strip() for item in stack_tags)):
            raise ValueError("subjects and stack_tags must be tuples of non-empty strings")
        normalized_paths = tuple(normalize_repo_path(path) for path in owned_paths)
        if find_secrets(project) or any(find_secrets(item) for item in (*owned_paths, *subjects)):
            raise ValueError("secret-like matching data cannot be used for retrieval")
        subject_set = set(subjects)
        tags = {tag.removeprefix("stack:") for tag in stack_tags}
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            candidates = db.execute("SELECT * FROM facts WHERE status=?", (ACTIVE,)).fetchall()
            matched = []
            for row in candidates:
                fact = _fact(row)
                if fact.scope == "project":
                    scoped = fact.origin_project == project
                else:
                    scoped = fact.scope[6:] in tags
                if not scoped:
                    continue
                try:
                    subject_path = normalize_repo_path(fact.subject)
                    path_match = _territories_intersect((subject_path,), normalized_paths)
                except ValueError:
                    path_match = False
                if not path_match and fact.subject not in subject_set:
                    continue
                recency = 2 ** (-max(0.0, current - fact.last_seen_at) /
                                RECENCY_HALF_LIFE_SECONDS)
                score = fact.confidence * recency
                matched.append((score, fact.fact_id, fact))
            matched.sort(key=lambda item: (-item[0], item[1]))
            selected: list[EvidenceFact] = []
            used = 0
            for _, _, fact in matched:
                cost = _estimate_tokens(fact.statement, fact.subject)
                if len(selected) >= limit or used + cost > token_budget:
                    continue
                selected.append(fact)
                used += cost
            payload = {"task_id": task_id, "fact_ids": [fact.fact_id for fact in selected]}
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            existing = db.execute("SELECT payload_json, kind FROM memory_events WHERE event_id=?",
                                  (event_id,)).fetchone()
            if existing:
                prior = json.loads(existing["payload_json"])
                if existing["kind"] != "injection" or prior.get("task_id") != task_id:
                    db.rollback()
                    raise ValueError("memory event ID was already used for different data")
                prior_ids = prior.get("fact_ids")
                if not isinstance(prior_ids, list):
                    db.rollback()
                    raise ValueError("stored injection event is malformed")
                selected_rows = []
                for fact_id in prior_ids:
                    row = db.execute("SELECT * FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
                    if row is None:
                        db.rollback()
                        raise ValueError("stored injection fact is no longer available")
                    selected_rows.append(_fact(row))
                selected = selected_rows
                used = sum(_estimate_tokens(fact.statement, fact.subject) for fact in selected)
                db.commit()
                return FactInjection(event_id, task_id, tuple(selected), used)
            self._event(db, event_id, "injection", payload, current)
            for fact in selected:
                db.execute("UPDATE facts SET injections=injections+1, last_seen_at=? WHERE fact_id=?",
                           (current, fact.fact_id))
                db.execute("INSERT INTO fact_injections(event_id, task_id, fact_id) VALUES (?, ?, ?)",
                           (event_id, task_id, fact.fact_id))
            db.commit()
        return FactInjection(event_id, task_id,
                             tuple(self.get_fact(fact.fact_id) for fact in selected), used)  # type: ignore[arg-type]

    def retrieve_for_task_record(self, *, event_id: str, task: TaskRecord, project: str,
                                 stack_tags: tuple[str, ...] = (), limit: int = 5,
                                 token_budget: int = 600,
                                 now: float | None = None) -> FactInjection:
        """Retrieve using task-owned paths, class, criteria, and named test evidence."""
        if not isinstance(task, TaskRecord):
            raise ValueError("task must be a validated TaskRecord")
        subjects = {task.task_id, task.title, *task.acceptance_criteria, *task.test_evidence}
        if task.task_class:
            subjects.add(task.task_class)
        for ref in task.test_evidence:
            parts = ref.split(":")
            if len(parts) >= 3 and parts[0] == "testrun":
                subjects.add(parts[2])
        return self.retrieve_for_task(
            event_id=event_id, task_id=task.task_id, project=project,
            owned_paths=task.owned_paths, subjects=tuple(sorted(subjects)),
            stack_tags=stack_tags, limit=limit, token_budget=token_budget, now=now,
        )

    def record_review_outcome(self, *, event_id: str, injection_event_id: str,
                              outcome: str, matching_fact_ids: tuple[str, ...] = (),
                              now: float | None = None) -> tuple[EvidenceFact, ...]:
        """Score facts used by a specific dispatch; rework losses require a match."""
        current = _clock(now)
        if outcome not in {"approved", "rework"}:
            raise ValueError("outcome must be approved or rework")
        if (not isinstance(event_id, str) or not event_id.strip()
                or not isinstance(injection_event_id, str) or not injection_event_id.strip()
                or not isinstance(matching_fact_ids, tuple)
                or not all(isinstance(fact_id, str) and fact_id.strip()
                           for fact_id in matching_fact_ids)
                or len(set(matching_fact_ids)) != len(matching_fact_ids)):
            raise ValueError("memory outcome IDs and matching fact IDs are invalid")
        with closing(self._connect()) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                prior = db.execute("SELECT payload_json FROM memory_events WHERE event_id=?",
                                   (event_id,)).fetchone()
                if prior:
                    payload = json.loads(prior["payload_json"])
                    if (payload.get("injection_event_id") != injection_event_id
                            or payload.get("outcome") != outcome
                            or payload.get("matching_fact_ids") != sorted(matching_fact_ids)):
                        raise ValueError("memory outcome event ID was reused")
                    ids = payload["updated_fact_ids"]
                    db.commit()
                    return tuple(self.get_fact(fact_id) for fact_id in ids)  # type: ignore[arg-type]
                injection = db.execute("SELECT task_id, fact_id FROM fact_injections WHERE event_id=?",
                                       (injection_event_id,)).fetchall()
                if not injection:
                    raise ValueError("unknown or empty fact injection event")
                if db.execute("SELECT 1 FROM injection_reviews WHERE injection_event_id=?",
                              (injection_event_id,)).fetchone():
                    raise ValueError("this fact injection already has a review outcome")
                injected = {row["fact_id"] for row in injection}
                matching = set(matching_fact_ids)
                if not matching.issubset(injected):
                    raise ValueError("matching facts must come from this injection")
                scored = injected if outcome == "approved" else matching
                for fact_id in scored:
                    row = db.execute("SELECT * FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
                    wins = row["wins"] + (outcome == "approved")
                    losses = row["losses"] + (outcome == "rework")
                    confidence = _confidence(wins, losses)
                    status = row["status"]
                    probation_at = row["probation_at"]
                    if (status == ACTIVE and row["injections"] >= PROBATION_MIN_INJECTIONS
                            and confidence < PROBATION_THRESHOLD):
                        status, probation_at = PROBATION, current
                    elif status == PROBATION and confidence >= REACTIVATION_THRESHOLD:
                        status, probation_at = ACTIVE, None
                    db.execute("UPDATE facts SET wins=?, losses=?, confidence=?, status=?, "
                               "probation_at=? WHERE fact_id=?",
                               (wins, losses, confidence, status, probation_at, fact_id))
                    db.execute("UPDATE fact_injections SET outcome=? WHERE event_id=? AND fact_id=?",
                               (outcome, injection_event_id, fact_id))
                payload = {"injection_event_id": injection_event_id, "outcome": outcome,
                           "matching_fact_ids": sorted(matching_fact_ids),
                           "updated_fact_ids": sorted(scored)}
                self._event(db, event_id, "outcome", payload, current)
                db.execute("INSERT INTO injection_reviews VALUES (?, ?, ?)",
                           (injection_event_id, event_id, outcome))
                db.commit()
            except Exception:
                db.rollback()
                raise
        return tuple(self.get_fact(fact_id) for fact_id in sorted(scored))  # type: ignore[arg-type]

    def fact_ids_for_injection(self, injection_event_id: str, *,
                               task_id: str | None = None) -> tuple[str, ...]:
        """Return the immutable fact set attached to a recorded injection event."""
        with closing(self._connect()) as db:
            event = db.execute("SELECT kind, payload_json FROM memory_events WHERE event_id=?",
                               (injection_event_id,)).fetchone()
            if event is None or event["kind"] != "injection":
                raise ValueError("unknown memory injection event")
            if task_id is not None and json.loads(event["payload_json"]).get("task_id") != task_id:
                raise ValueError("memory injection belongs to another task")
            rows = db.execute("SELECT fact_id FROM fact_injections WHERE event_id=? "
                              "ORDER BY fact_id", (injection_event_id,)).fetchall()
        return tuple(row["fact_id"] for row in rows)


    def retire_expired_probation(self, *, now: float | None = None) -> tuple[str, ...]:
        """Retire facts that have remained in probation for the configured grace period."""
        current = _clock(now)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT fact_id, probation_at FROM facts WHERE status=?",
                              (PROBATION,)).fetchall()
            retired = tuple(sorted(row["fact_id"] for row in rows
                                   if row["probation_at"] is not None
                                   and current - row["probation_at"] >= RETIRE_AFTER_SECONDS))
            for fact_id in retired:
                db.execute("UPDATE facts SET status=? WHERE fact_id=?", (RETIRED, fact_id))
                self._event(db, f"fact-retired:{fact_id}:{int(current)}", "retired",
                            {"fact_id": fact_id, "reason": "probation_expired"}, current)
            db.commit()
        return retired

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @staticmethod
    def _event(db: sqlite3.Connection, event_id: str, kind: str,
               payload: dict, now: float) -> None:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        db.execute("INSERT INTO memory_events VALUES (?, ?, ?, ?)",
                   (event_id, kind, encoded, now))


def render_fact_injection(injection: FactInjection, *, task_id: str) -> str:
    """Render only a task-bound, cited injection as explicit advisory context."""
    if not isinstance(injection, FactInjection) or injection.task_id != task_id:
        raise ValueError("memory injection must be bound to the current task")
    if not isinstance(injection.event_id, str) or not injection.event_id.strip():
        raise ValueError("memory injection requires a ledger event ID")
    lines = ["CODEXDEVTEAM EVIDENCE MEMORY (advisory; verify against the current task):"]
    for fact in injection.facts:
        if (not isinstance(fact, EvidenceFact) or not fact.evidence
                or find_secrets(fact.fact_id) or find_secrets(fact.statement)
                or any(find_secrets(ref) for ref in fact.evidence)):
            raise ValueError("memory injection contains an invalid or secret-like fact")
        lines.append(f"- [memory-fact:{fact.fact_id}; {fact.kind}] "
                     f"{fact.subject}: {fact.statement}")
        lines.append("  Evidence: " + ", ".join(fact.evidence))
    return "\n".join(lines) if injection.facts else ""


def _clock(now: float | None) -> float:
    current = time.time() if now is None else now
    if isinstance(current, bool) or not isinstance(current, (int, float)) or not math.isfinite(current):
        raise ValueError("memory timestamp must be finite")
    return float(current)


def _confidence(wins: int, losses: int) -> float:
    return (SEED_ALPHA + wins) / (SEED_ALPHA + SEED_BETA + wins + losses)


def _estimate_tokens(statement: str, subject: str) -> int:
    # Stable budget estimate without coupling the kernel to a provider tokenizer.
    return max(1, math.ceil(len((statement + " " + subject).encode("utf-8")) / 4))


def _fact(row: sqlite3.Row) -> EvidenceFact:
    return EvidenceFact(row["fact_id"], row["kind"], row["scope"], row["subject"],
                        row["statement"], tuple(json.loads(row["evidence_json"])),
                        row["created_at"], row["last_seen_at"], row["injections"],
                        row["wins"], row["losses"], row["confidence"], row["status"],
                        row["origin_project"], row["probation_at"])
