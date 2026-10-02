"""Transactional local state and exclusive HEAD lease backed by SQLite."""

import json
import hashlib
import hmac
import secrets
import sqlite3
import time
import math
import re
import os
import tempfile
import stat
from contextlib import closing
from dataclasses import dataclass, field, replace
from pathlib import Path

from .protocol import TaskRecord, _territories_intersect, validate_task_set
from .identity import WorkerIdentity
from .tasks import TaskState, allowed_transition
from .gate import GateResult
from .review import ReviewVerdict, gate_artifact_findings, review_digest, validate_review
from .health import StagnationSample, is_stagnant, remedial_kind, update_streak
from .dispatch import assign_task as make_assignment
from .registry import WorkerRegistry
from .runtime import InvocationResult
from .control import ControlMessage, validate_control
from .test_runs import TestRunCache, TestRunResult
from .process_identity import ProcessIdentity
from .process_reaper import ProcessReapResult, reap_managed_process
from .plan_markdown import PlanWriteConflict, patch_plan_task_state
from .plan_archive import append_archive_blocks, plan_archive
from .secrets import find_secrets


STATE_SCHEMA_VERSION = 7
_STATE_SCHEMA_COLUMNS = {
    "head_lease": {"singleton", "system_id", "instance_id", "generation", "expires_at", "token_hash"},
    "state_events": {"event_id", "generation", "payload_json", "created_at"},
    "tasks": {"task_id", "payload_json", "updated_at"},
    "supervisor_mode": {"singleton", "mode", "reason", "changed_at", "generation"},
    "maintenance_schedule": {"maintenance_key", "completed_at", "event_id", "generation"},
    "escalations": {"escalation_key", "task_id", "severity", "message", "opened_at",
                    "last_notified_at", "notification_count", "status", "resolved_at", "generation"},
    "escalation_notifications": {"notification_id", "escalation_key", "task_id", "severity",
                                 "message", "notification_count", "created_at", "available_at",
                                 "attempt_count", "claim_token", "claim_expires_at", "last_error_code",
                                 "delivered_at", "cancelled_at"},
    "task_health": {"task_id", "streak", "reset_count", "last_changed", "last_denials",
                    "updated_at", "generation"},
    "task_invocation_liveness": {"task_id", "invocation_id", "state", "started_at",
                                 "heartbeat_at", "generation", "termination_hmac_key",
                                 "process_pid", "process_start_token", "process_group_id",
                                 "process_containment_ref"},
    "gate_receipts": {"task_id", "sha", "fingerprint", "artifact_path", "artifact_sha256",
                      "registered_at", "generation"},
    "gate_attempt_receipts": {"attempt_event_id", "task_id", "sha", "fingerprint", "artifact_path",
                              "artifact_sha256", "status", "created_at", "generation"},
    "test_run_receipts": {"task_id", "evidence_ref", "sha", "test_name", "environment_fingerprint",
                          "artifact_path", "artifact_sha256", "output_sha256", "registered_at", "generation"},
    "territory_conflicts": {"conflict_event_id", "task_id", "conflicts_json", "created_at", "generation"},
    "invocation_receipts": {"invocation_id", "task_id", "purpose", "unit_id", "runtime", "model",
                            "status", "exit_code", "output_sha256", "started_at", "finished_at",
                            "generation", "review_sha", "gate_fingerprint",
                            "process_tree_cancel_method", "process_tree_cancel_verified",
                            "process_tree_cancel_exit_code"},
    "invocation_cancellation_receipts": {"invocation_id", "task_id", "method", "exit_code",
                                          "observed_at", "receipt_sha256", "evidence_source",
                                          "generation"},
    "task_archive": {"task_id", "payload_json", "payload_sha256", "archived_at", "generation"},
    "handover_imports": {"event_id", "source_plan_sha256", "mapping_sha256", "imported_at", "generation"},
    "historical_task_ids": {"task_id", "source_plan_sha256", "imported_at", "generation"},
    "handover_context_fields": {"task_id", "field_name", "occurrence", "field_value",
                                "source_plan_sha256", "generation"},
    "plan_projection_outbox": {"event_id", "project_root", "expected_sha256", "projected_sha256",
                               "projected_text", "created_at", "generation"},
}


@dataclass(frozen=True, slots=True)
class HeadLease:
    system_id: str
    instance_id: str
    generation: int
    expires_at: float
    token: str = field(repr=False)


class LeaseError(RuntimeError):
    """Raised when a caller lacks the current exclusive HEAD lease."""


class ControlRejected(LeaseError):
    """A current HEAD lease was valid, but the submitted CONTROL request was invalid."""


class PlanProjectionPending(PlanWriteConflict):
    """Task state committed; its durable PLAN projection needs leased recovery."""

    def __init__(self, event_id: str, detail: str):
        self.event_id = event_id
        super().__init__(f"task state committed; PLAN projection {event_id} remains pending: {detail}")


def _exact_conflict_subject(left: str, right: str) -> str | None:
    """Return a useful subject only when an overlap identifies a concrete territory."""
    left_glob = any(char in left for char in "*?[")
    right_glob = any(char in right for char in "*?[")
    if not left_glob:
        return left
    if not right_glob:
        return right
    return left if left == right else None


def _resolve_plan_path(project_root: str | Path) -> tuple[Path, Path]:
    raw_root = Path(project_root)
    if raw_root.is_symlink():
        raise PlanWriteConflict("project root cannot be a symlink")
    try:
        root = raw_root.resolve(strict=True)
    except OSError as exc:
        raise PlanWriteConflict(f"project root is unavailable: {exc}") from exc
    if not root.is_dir():
        raise PlanWriteConflict("project root must be a directory")
    plan_path = root / "PLAN.md"
    if plan_path.is_symlink() or not plan_path.is_file():
        raise PlanWriteConflict("PLAN.md must be an existing regular file")
    return root, plan_path


def _atomic_replace_plan(root: Path, plan_path: Path, content: bytes,
                         expected_sha256: str) -> None:
    if plan_path.is_symlink() or not plan_path.is_file():
        raise PlanWriteConflict("PLAN.md became unavailable or a symlink")
    current = plan_path.read_bytes()
    if hashlib.sha256(current).hexdigest() != expected_sha256:
        raise PlanWriteConflict("PLAN changed before atomic projection replacement")
    mode = stat.S_IMODE(plan_path.stat().st_mode)
    fd, raw_temp = tempfile.mkstemp(prefix=".codexdevteam-plan-", dir=root)
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temp_path, mode)
        except OSError:
            pass
        if plan_path.is_symlink() or hashlib.sha256(plan_path.read_bytes()).hexdigest() != expected_sha256:
            raise PlanWriteConflict("PLAN changed before atomic projection replacement")
        os.replace(temp_path, plan_path)
        if hashlib.sha256(plan_path.read_bytes()).hexdigest() != hashlib.sha256(content).hexdigest():
            raise PlanWriteConflict("atomic PLAN replacement could not be verified")
        if os.name != "nt":
            try:
                directory_fd = os.open(root, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
    finally:
        temp_path.unlink(missing_ok=True)


def _prepare_plan_projection(project_root: str | Path, task_id: str,
                             target: TaskState, assigned_worker: str | None, *,
                             expected_sha256: str | None = None) -> dict:
    root, plan_path = _resolve_plan_path(project_root)
    try:
        source_bytes = plan_path.read_bytes()
        source = source_bytes.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise PlanWriteConflict(f"could not read PLAN.md: {exc}") from exc
    actual_sha = hashlib.sha256(source_bytes).hexdigest()
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ValueError("expected_plan_sha256 must be a lowercase SHA-256 digest")
        if actual_sha != expected_sha256:
            raise PlanWriteConflict("PLAN changed since it was read")
    projected_text, projected_sha = patch_plan_task_state(
        source, task_id=task_id, state=target, assigned_worker=assigned_worker,
        expected_sha256=actual_sha,
    )
    return {
        "project_root": str(root),
        "expected_sha256": actual_sha,
        "projected_sha256": projected_sha,
        "projected_text": projected_text.encode("utf-8"),
    }


class StateStore:
    """Small transactional state store; lease and state writes share a DB lock.

    Lease expiry alone never permits an uncoordinated write. Every state write
    checks owner, generation, and expiry inside the same `BEGIN IMMEDIATE`
    transaction that records the event, fencing stale processes after handover.
    """

    SCHEMA_VERSION = STATE_SCHEMA_VERSION

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > STATE_SCHEMA_VERSION:
                raise LeaseError(
                    f"state database schema {version} is newer than supported "
                    f"schema {STATE_SCHEMA_VERSION}"
                )
            schema_sql = """
                CREATE TABLE IF NOT EXISTS head_lease (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    system_id TEXT NOT NULL,
                    instance_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    expires_at REAL NOT NULL,
                    token_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS state_events (
                    event_id TEXT PRIMARY KEY,
                    generation INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS supervisor_mode (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    mode TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    changed_at REAL NOT NULL,
                    generation INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS maintenance_schedule (
                    maintenance_key TEXT PRIMARY KEY,
                    completed_at REAL NOT NULL,
                    event_id TEXT NOT NULL,
                    generation INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS escalations (
                    escalation_key TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    opened_at REAL NOT NULL,
                    last_notified_at REAL NOT NULL,
                    notification_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    resolved_at REAL,
                    generation INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS escalation_notifications (
                    notification_id TEXT PRIMARY KEY,
                    escalation_key TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    notification_count INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    available_at REAL NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    claim_token TEXT,
                    claim_expires_at REAL,
                    last_error_code TEXT,
                    delivered_at REAL,
                    cancelled_at REAL
                );
                CREATE TABLE IF NOT EXISTS task_health (
                    task_id TEXT PRIMARY KEY,
                    streak INTEGER NOT NULL,
                    reset_count INTEGER NOT NULL,
                    last_changed INTEGER NOT NULL,
                    last_denials INTEGER NOT NULL,
                    updated_at REAL NOT NULL,
                    generation INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_invocation_liveness (
                    task_id TEXT NOT NULL,
                    invocation_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    heartbeat_at REAL NOT NULL,
                    generation INTEGER NOT NULL,
                    termination_hmac_key TEXT,
                    process_pid INTEGER,
                    process_start_token TEXT,
                    process_group_id INTEGER,
                    process_containment_ref TEXT
                );
                CREATE TABLE IF NOT EXISTS gate_receipts (
                    task_id TEXT NOT NULL,
                    sha TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    artifact_path TEXT NOT NULL,
                    artifact_sha256 TEXT NOT NULL,
                    registered_at REAL NOT NULL,
                    generation INTEGER NOT NULL,
                    PRIMARY KEY(task_id, sha, fingerprint)
                );
                CREATE TABLE IF NOT EXISTS gate_attempt_receipts (
                    attempt_event_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    sha TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    artifact_path TEXT NOT NULL,
                    artifact_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    generation INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS test_run_receipts (
                    task_id TEXT NOT NULL,
                    evidence_ref TEXT NOT NULL,
                    sha TEXT NOT NULL,
                    test_name TEXT NOT NULL,
                    environment_fingerprint TEXT NOT NULL,
                    artifact_path TEXT NOT NULL,
                    artifact_sha256 TEXT NOT NULL,
                    output_sha256 TEXT NOT NULL,
                    registered_at REAL NOT NULL,
                    generation INTEGER NOT NULL,
                    PRIMARY KEY(task_id, evidence_ref)
                );
                CREATE TABLE IF NOT EXISTS territory_conflicts (
                    conflict_event_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    conflicts_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    generation INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS invocation_receipts (
                    invocation_id TEXT PRIMARY KEY,
                    task_id TEXT,
                    purpose TEXT NOT NULL,
                    unit_id TEXT NOT NULL,
                    runtime TEXT NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    exit_code INTEGER,
                    output_sha256 TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL NOT NULL,
                    generation INTEGER NOT NULL,
                    review_sha TEXT,
                    gate_fingerprint TEXT,
                    process_tree_cancel_method TEXT,
                    process_tree_cancel_verified INTEGER,
                    process_tree_cancel_exit_code INTEGER
                );
                CREATE TABLE IF NOT EXISTS invocation_cancellation_receipts (
                    invocation_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    method TEXT NOT NULL,
                    exit_code INTEGER,
                    observed_at REAL NOT NULL,
                    receipt_sha256 TEXT NOT NULL,
                    evidence_source TEXT NOT NULL,
                    generation INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_archive (
                    task_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    archived_at REAL NOT NULL,
                    generation INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS plan_projection_outbox (
                    event_id TEXT PRIMARY KEY,
                    project_root TEXT NOT NULL,
                    expected_sha256 TEXT NOT NULL,
                    projected_sha256 TEXT NOT NULL,
                    projected_text BLOB NOT NULL,
                    created_at REAL NOT NULL,
                    generation INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS handover_imports (
                    event_id TEXT PRIMARY KEY,
                    source_plan_sha256 TEXT NOT NULL,
                    mapping_sha256 TEXT NOT NULL,
                    imported_at REAL NOT NULL,
                    generation INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS historical_task_ids (
                    task_id TEXT PRIMARY KEY,
                    source_plan_sha256 TEXT NOT NULL,
                    imported_at REAL NOT NULL,
                    generation INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS handover_context_fields (
                    task_id TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    occurrence INTEGER NOT NULL,
                    field_value TEXT NOT NULL,
                    source_plan_sha256 TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    PRIMARY KEY(task_id, field_name, occurrence)
                );
            """
            db.executescript("BEGIN IMMEDIATE;\n" + schema_sql)
            try:
                invocation_columns = {row["name"] for row in db.execute(
                    "PRAGMA table_info(invocation_receipts)").fetchall()}
                for column, sql_type in (
                    ("process_tree_cancel_method", "TEXT"),
                    ("process_tree_cancel_verified", "INTEGER"),
                    ("process_tree_cancel_exit_code", "INTEGER"),
                ):
                    if column not in invocation_columns:
                        db.execute(f"ALTER TABLE invocation_receipts ADD COLUMN {column} {sql_type}")
                liveness_columns = {row["name"] for row in db.execute(
                    "PRAGMA table_info(task_invocation_liveness)").fetchall()}
                if "termination_hmac_key" not in liveness_columns:
                    db.execute("ALTER TABLE task_invocation_liveness "
                               "ADD COLUMN termination_hmac_key TEXT")
                for column, sql_type in (("process_pid", "INTEGER"),
                                         ("process_start_token", "TEXT"),
                                         ("process_group_id", "INTEGER"),
                                         ("process_containment_ref", "TEXT")):
                    if column not in liveness_columns:
                        db.execute(f"ALTER TABLE task_invocation_liveness "
                                   f"ADD COLUMN {column} {sql_type}")
                for table, required_columns in _STATE_SCHEMA_COLUMNS.items():
                    actual_columns = {row["name"] for row in db.execute(
                        f"PRAGMA table_info({table})").fetchall()}
                    missing_columns = required_columns - actual_columns
                    if missing_columns:
                        raise LeaseError(
                            f"state database table {table} is incompatible; missing columns: "
                            + ", ".join(sorted(missing_columns))
                        )
                db.execute(f"PRAGMA user_version={STATE_SCHEMA_VERSION}")
                db.commit()
            except Exception:
                db.rollback()
                raise

    @property
    def cancellation_receipt_directory(self) -> Path:
        """Private adjacent directory used for lease-independent kill receipts."""
        return Path(str(self.path) + ".cancellations")

    def create_task_snapshot(self, destination: str | Path, task_id: str, *,
                             source_path: str | Path | None = None) -> Path:
        """Create a minimal read-only snapshot for one builder's hook checks.

        The snapshot contains only the current task and other active tasks, so
        the hook can enforce current territory reservations without exposing
        event ledgers, receipts, escalation messages, or unrelated task data.
        """
        source = Path(source_path) if source_path is not None else self.path
        if source.is_symlink():
            raise LeaseError("snapshot source database cannot be a symlink")
        source = source.resolve(strict=True)
        if not source.is_file():
            raise LeaseError("snapshot source must be a regular database file")
        target = Path(destination)
        parent = target.parent
        if parent.is_symlink() or not parent.is_dir():
            raise LeaseError("snapshot destination parent must be a real directory")
        parent = parent.resolve(strict=True)
        target = parent / target.name
        if target.exists() or target.is_symlink():
            raise LeaseError("snapshot destination already exists")
        if target == source:
            raise LeaseError("snapshot destination must differ from source database")

        source_uri = source.as_uri() + "?mode=ro"
        active_states = {TaskState.CLAIMED, TaskState.IN_PROGRESS, TaskState.NEEDS_REVIEW}
        with closing(sqlite3.connect(source_uri, uri=True, timeout=10)) as source_db:
            source_db.execute("BEGIN")
            rows = source_db.execute(
                "SELECT task_id, payload_json, updated_at FROM tasks ORDER BY task_id"
            ).fetchall()
        selected = []
        task_found = False
        for row in rows:
            task = TaskRecord.from_dict(json.loads(row[1]))
            if task.task_id == task_id:
                task_found = True
                if task.state not in {TaskState.CLAIMED, TaskState.IN_PROGRESS}:
                    raise LeaseError("builder task snapshot requires a claimed active task")
            if task.task_id == task_id or task.state in active_states:
                policy_task = TaskRecord(
                    task_id=task.task_id, title="Hook policy snapshot", state=task.state,
                    assigned_worker=task.assigned_worker, priority="low",
                    owned_paths=task.owned_paths, protected_grants=task.protected_grants,
                )
                selected.append((task.task_id,
                                 json.dumps(policy_task.to_dict(), sort_keys=True,
                                            separators=(",", ":")),
                                 row[2]))
        if not task_found:
            raise LeaseError(f"builder task snapshot is missing task {task_id}")

        fd, temporary_name = tempfile.mkstemp(prefix=".codexdevteam-state-",
                                              suffix=".sqlite", dir=parent)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            with closing(sqlite3.connect(str(temporary), timeout=10)) as target_db:
                target_db.execute(
                    "CREATE TABLE tasks (task_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, "
                    "updated_at REAL NOT NULL)"
                )
                target_db.executemany("INSERT INTO tasks VALUES(?, ?, ?)", selected)
                target_db.commit()
                check = target_db.execute("PRAGMA integrity_check").fetchone()
                if check is None or check[0] != "ok":
                    raise LeaseError("snapshot database failed integrity check")
            temporary.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
            os.replace(temporary, target)
            return target
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    def acquire_head(self, system_id: str, instance_id: str, *,
                     ttl_seconds: float = 30.0, now: float | None = None,
                     takeover_confirmed: bool = False) -> HeadLease:
        if not system_id.strip() or not instance_id.strip() or ttl_seconds <= 0:
            raise ValueError("system_id, instance_id and positive ttl_seconds are required")
        current = time.time() if now is None else now
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM head_lease WHERE singleton = 1").fetchone()
            if row and row["expires_at"] > current:
                raise LeaseError("another live HEAD lease is active")
            if row and not takeover_confirmed:
                raise LeaseError("prior HEAD lease exists; explicit handover confirmation is required")
            if row:
                generation = row["generation"] + 1
            else:
                generation = 1
            expiry = current + ttl_seconds
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            db.execute("""INSERT INTO head_lease(singleton, system_id, instance_id, generation, expires_at, token_hash)
                          VALUES(1, ?, ?, ?, ?, ?)
                          ON CONFLICT(singleton) DO UPDATE SET system_id=excluded.system_id,
                          instance_id=excluded.instance_id, generation=excluded.generation,
                          expires_at=excluded.expires_at, token_hash=excluded.token_hash""",
                       (system_id, instance_id, generation, expiry, token_hash))
            db.commit()
            return HeadLease(system_id, instance_id, generation, expiry, token)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def renew_head(self, lease: HeadLease, *, ttl_seconds: float = 30.0,
                   now: float | None = None) -> HeadLease:
        current = time.time() if now is None else now
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            expiry = current + ttl_seconds
            db.execute("UPDATE head_lease SET expires_at=? WHERE singleton=1", (expiry,))
            db.commit()
            return HeadLease(lease.system_id, lease.instance_id, lease.generation, expiry, lease.token)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def release_head(self, lease: HeadLease, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            # Keep the generation tombstone so fencing numbers remain monotonic.
            db.execute("UPDATE head_lease SET expires_at=0 WHERE singleton=1")
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def record_event(self, lease: HeadLease, event_id: str, payload: dict, *,
                     now: float | None = None) -> bool:
        """Record a single-writer event; return False for an already-seen ID."""
        if not event_id.strip():
            raise ValueError("event_id must be non-empty")
        current = time.time() if now is None else now
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            inserted = self._insert_event(db, event_id, lease.generation, payload_json, current)
            db.commit()
            return inserted
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def events(self) -> list[dict]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT event_id, generation, payload_json, created_at "
                              "FROM state_events ORDER BY created_at, event_id").fetchall()
        return [{"event_id": row["event_id"], "generation": row["generation"],
                 "payload": json.loads(row["payload_json"]), "created_at": row["created_at"]}
                for row in rows]

    def verified_test_run_events(self) -> list[dict]:
        """Return registered passing-test events whose typed rows and artifacts still agree."""
        with closing(self._connect()) as db:
            receipts = db.execute("SELECT * FROM test_run_receipts ORDER BY registered_at, task_id, evidence_ref").fetchall()
        events = self.events()
        verified: list[dict] = []
        for receipt in receipts:
            artifact_path = Path(receipt["artifact_path"])
            try:
                if artifact_path.is_symlink() or not artifact_path.is_file():
                    continue
                if artifact_path.resolve(strict=True) != artifact_path:
                    continue
                artifact_bytes = artifact_path.read_bytes()
                if hashlib.sha256(artifact_bytes).hexdigest() != receipt["artifact_sha256"]:
                    continue
                artifact = json.loads(artifact_bytes.decode("utf-8"))
                log_path = Path(artifact["log_path"])
                if log_path.is_symlink() or not log_path.is_file() or log_path.resolve(strict=True) != log_path:
                    continue
                if (hashlib.sha256(log_path.read_bytes()).hexdigest() != receipt["output_sha256"]
                        or not artifact.get("passed")
                        or artifact.get("sha") != receipt["sha"]
                        or artifact.get("name") != receipt["test_name"]
                        or artifact.get("environment_fingerprint") != receipt["environment_fingerprint"]
                        or artifact.get("output_sha256") != receipt["output_sha256"]):
                    continue
            except (OSError, UnicodeError, ValueError, KeyError, TypeError):
                continue
            candidates = [event for event in events
                          if event["payload"].get("type") == "test_run.registered"
                          and event["payload"].get("task_id") == receipt["task_id"]
                          and event["payload"].get("evidence_ref") == receipt["evidence_ref"]
                          and event["payload"].get("sha") == receipt["sha"]
                          and event["payload"].get("test_name") == receipt["test_name"]
                          and event["payload"].get("artifact_sha256") == receipt["artifact_sha256"]]
            if candidates:
                verified.append(min(candidates, key=lambda event: event["event_id"]))
        return verified

    def verified_territory_conflict_events(self) -> list[dict]:
        """Return typed, HEAD-recorded territory denials joined to their journal event."""
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM territory_conflicts ORDER BY created_at, conflict_event_id").fetchall()
        events = {event["event_id"]: event for event in self.events()}
        verified = []
        for row in rows:
            event = events.get(row["conflict_event_id"])
            if event is None:
                continue
            payload = event["payload"]
            try:
                stored = json.loads(row["conflicts_json"])
            except (ValueError, TypeError):
                continue
            if (payload.get("type") == "territory.conflict"
                    and payload.get("task_id") == row["task_id"]
                    and payload.get("conflicts") == stored
                    and event["created_at"] == row["created_at"]):
                verified.append(event)
        return verified

    def verified_review_events(self) -> list[dict]:
        """Return journal reviews still joined to their gate and checker receipts."""
        events = self.events()
        verified = []
        with closing(self._connect()) as db:
            for event in events:
                payload = event["payload"]
                if payload.get("type") != "task.reviewed":
                    continue
                try:
                    checker = payload["checker"]
                    refs = payload["evidence_refs"]
                    if (not isinstance(refs, list)
                            or not all(isinstance(ref, str) and ref.strip() for ref in refs)
                            or len(set(refs)) != len(refs)):
                        continue
                    verdict = ReviewVerdict(
                        task_id=payload["task_id"], sha=payload["sha"],
                        gate_fingerprint=payload["gate_fingerprint"],
                        checker=WorkerIdentity(checker["unit_id"], "review", "standard",
                                               checker["runtime"], checker["model"]),
                        decision=payload["decision"], rationale=payload["rationale"],
                        evidence_refs=tuple(refs),
                    )
                    if payload.get("review_digest") != review_digest(verdict):
                        continue
                    gate = db.execute(
                        "SELECT * FROM gate_receipts WHERE task_id=? AND sha=? AND fingerprint=?",
                        (verdict.task_id, verdict.sha, verdict.gate_fingerprint),
                    ).fetchone()
                    invocation_id = payload["checker_invocation_id"]
                    invocation = db.execute(
                        "SELECT * FROM invocation_receipts WHERE invocation_id=?",
                        (invocation_id,),
                    ).fetchone()
                    if (gate is None or self._file_sha256(gate["artifact_path"]) != gate["artifact_sha256"]
                            or invocation is None or invocation["task_id"] != verdict.task_id
                            or invocation["purpose"] != "checker" or invocation["status"] != "succeeded"
                            or invocation["exit_code"] != 0
                            or invocation["unit_id"] != checker["unit_id"]
                            or invocation["runtime"] != checker["runtime"]
                            or invocation["model"] != checker["model"]
                            or invocation["review_sha"] != verdict.sha
                            or invocation["gate_fingerprint"] != verdict.gate_fingerprint):
                        continue
                    task_row = db.execute("SELECT payload_json FROM tasks WHERE task_id=?",
                                          (verdict.task_id,)).fetchone()
                    if task_row is None:
                        continue
                    enriched = dict(event)
                    enriched["task_owned_paths"] = list(
                        TaskRecord.from_dict(json.loads(task_row["payload_json"])).owned_paths
                    )
                    verified.append(enriched)
                except (KeyError, TypeError, ValueError):
                    continue
        return verified

    def verified_gate_attempt_events(self) -> list[dict]:
        """Return completed gate attempts joined to intact artifacts and typed rows."""
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM gate_attempt_receipts ORDER BY created_at, attempt_event_id").fetchall()
        events = {event["event_id"]: event for event in self.events()}
        verified = []
        for row in rows:
            event = events.get(row["attempt_event_id"])
            if event is None or event["created_at"] != row["created_at"]:
                continue
            payload = event["payload"]
            if (payload.get("type") != "gate.attempted"
                    or payload.get("task_id") != row["task_id"]
                    or payload.get("sha") != row["sha"]
                    or payload.get("fingerprint") != row["fingerprint"]
                    or payload.get("artifact_sha256") != row["artifact_sha256"]
                    or payload.get("status") != row["status"]
                    or Path(row["artifact_path"]).is_symlink()
                    or self._file_sha256(row["artifact_path"]) != row["artifact_sha256"]):
                continue
            try:
                artifact = json.loads(Path(row["artifact_path"]).read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError):
                continue
            checks = artifact.get("checks")
            if not isinstance(checks, dict):
                continue
            statuses = {name: item.get("status") for name, item in checks.items()
                        if isinstance(item, dict)}
            if (artifact.get("task_id") != row["task_id"] or artifact.get("sha") != row["sha"]
                    or artifact.get("fingerprint") != row["fingerprint"]
                    or artifact.get("status") != row["status"]
                    or statuses != payload.get("checks")
                    or artifact.get("new_failures", []) != payload.get("new_failures")
                    or artifact.get("inherited_failures", []) != payload.get("inherited_failures")):
                continue
            verified.append(event)
        return verified

    def invocation_summary(self, *, rates: tuple = ()) -> tuple:
        """Return provider-neutral grouped outcomes and priced usage totals."""
        from .usage import summarize_invocations

        return summarize_invocations(self.events(), rates=rates)

    def set_supervisor_mode(self, lease: HeadLease, mode: str, *, event_id: str,
                            reason: str = "", now: float | None = None) -> bool:
        """Durably park or resume supervision under the exclusive HEAD lease."""
        if mode not in {"running", "parked"}:
            raise ValueError("supervisor mode must be running or parked")
        if mode == "parked" and not reason.strip():
            raise ValueError("parking requires an operator-visible reason")
        current = time.time() if now is None else now
        payload = {"type": "supervisor.mode_changed", "mode": mode, "reason": reason}
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            if self._event_exists(db, event_id, payload_json):
                db.commit()
                return False
            db.execute("INSERT INTO supervisor_mode VALUES(1, ?, ?, ?, ?) "
                       "ON CONFLICT(singleton) DO UPDATE SET mode=excluded.mode, "
                       "reason=excluded.reason, changed_at=excluded.changed_at, "
                       "generation=excluded.generation",
                       (mode, reason, current, lease.generation))
            self._insert_event(db, event_id, lease.generation, payload_json, current)
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get_supervisor_mode(self) -> dict:
        """Return durable park/resume state; an uninitialized supervisor is parked."""
        with closing(self._connect()) as db:
            row = db.execute("SELECT mode, reason, changed_at, generation "
                             "FROM supervisor_mode WHERE singleton=1").fetchone()
        if row is None:
            return {"mode": "parked", "reason": "not started", "changed_at": None,
                    "generation": None}
        return dict(row)

    def maintenance_completed_at(self, maintenance_key: str) -> float | None:
        """Read the last successful completion time for one maintenance job."""
        if (not isinstance(maintenance_key, str)
                or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", maintenance_key)):
            raise ValueError("maintenance_key is invalid")
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT completed_at FROM maintenance_schedule WHERE maintenance_key=?",
                (maintenance_key,),
            ).fetchone()
        return float(row["completed_at"]) if row else None

    def record_maintenance_completion(self, lease: HeadLease, maintenance_key: str, *,
                                      event_id: str, now: float | None = None) -> bool:
        """Record one successful maintenance run under the current HEAD lease."""
        if (not isinstance(maintenance_key, str)
                or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", maintenance_key)):
            raise ValueError("maintenance_key is invalid")
        current = time.time() if now is None else now
        payload = {"type": "supervisor.maintenance_completed",
                   "maintenance_key": maintenance_key}
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            prior = db.execute("SELECT payload_json FROM state_events WHERE event_id=?",
                               (event_id,)).fetchone()
            if prior is not None:
                if prior["payload_json"] != payload_json:
                    raise LeaseError("maintenance event ID was already used for another operation")
                db.commit()
                return False
            db.execute(
                "INSERT INTO maintenance_schedule VALUES(?, ?, ?, ?) "
                "ON CONFLICT(maintenance_key) DO UPDATE SET completed_at=excluded.completed_at, "
                "event_id=excluded.event_id, generation=excluded.generation",
                (maintenance_key, current, event_id, lease.generation),
            )
            self._insert_event(db, event_id, lease.generation, payload_json, current)
            self._assert_current(db, lease, current)
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def record_escalation(self, lease: HeadLease, *, escalation_key: str, task_id: str,
                          severity: str, message: str, event_id: str,
                          remind_after_seconds: float = 14400.0,
                          now: float | None = None) -> dict:
        """Persist/dedupe an escalation; return whether its notification is due."""
        if not escalation_key.strip() or not task_id.strip() or not message.strip():
            raise ValueError("escalation key, task_id and message are required")
        if find_secrets(message):
            message = "Sensitive escalation details were redacted before delivery."
        if severity not in {"critical", "high", "medium", "low"}:
            raise ValueError("severity must be critical, high, medium, or low")
        if remind_after_seconds < 0:
            raise ValueError("remind_after_seconds must be non-negative")
        current = time.time() if now is None else now
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            prior_event = db.execute("SELECT payload_json, created_at FROM state_events WHERE event_id=?",
                                     (event_id,)).fetchone()
            if prior_event is not None:
                recorded = json.loads(prior_event["payload_json"])
                if (recorded.get("type") != "escalation.recorded"
                        or recorded.get("escalation_key") != escalation_key
                        or recorded.get("task_id") != task_id
                        or recorded.get("severity") != severity
                        or recorded.get("message") != message):
                    raise LeaseError("event_id was already used for a different escalation")
                if recorded.get("notify") and recorded.get("notification_id"):
                    db.execute(
                        "INSERT OR IGNORE INTO escalation_notifications "
                        "(notification_id, escalation_key, task_id, severity, message, "
                        "notification_count, created_at, available_at, attempt_count) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, 0)",
                        (recorded["notification_id"], escalation_key, task_id, severity,
                         message, recorded["notification_count"], prior_event["created_at"],
                         prior_event["created_at"]),
                    )
                db.commit()
                return {"notify": recorded["notify"],
                        "notification_count": recorded["notification_count"],
                        "notification_id": recorded.get("notification_id"),
                        "duplicate": True}
            row = db.execute("SELECT * FROM escalations WHERE escalation_key=?",
                             (escalation_key,)).fetchone()
            if row and row["status"] == "open":
                notify = current - row["last_notified_at"] >= remind_after_seconds
                count = row["notification_count"] + int(notify)
                last_notified = current if notify else row["last_notified_at"]
                opened_at = row["opened_at"]
            else:
                notify, count, last_notified, opened_at = True, 1, current, current
            notification_id = hashlib.sha256(
                f"{escalation_key}\0{count}\0{event_id}".encode("utf-8")
            ).hexdigest()
            payload = {"type": "escalation.recorded", "escalation_key": escalation_key,
                       "task_id": task_id, "severity": severity, "message": message,
                       "notify": notify, "notification_count": count,
                       "notification_id": notification_id if notify else None}
            payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            db.execute("INSERT INTO escalations VALUES(?, ?, ?, ?, ?, ?, ?, 'open', NULL, ?) "
                       "ON CONFLICT(escalation_key) DO UPDATE SET task_id=excluded.task_id, "
                       "severity=excluded.severity, message=excluded.message, opened_at=excluded.opened_at, "
                       "last_notified_at=excluded.last_notified_at, "
                       "notification_count=excluded.notification_count, status='open', "
                       "resolved_at=NULL, generation=excluded.generation",
                       (escalation_key, task_id, severity, message, opened_at,
                        last_notified, count, lease.generation))
            self._insert_event(db, event_id, lease.generation, payload_json, current)
            if notify:
                db.execute(
                    "INSERT OR IGNORE INTO escalation_notifications "
                    "(notification_id, escalation_key, task_id, severity, message, "
                    "notification_count, created_at, available_at, attempt_count) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, 0)",
                    (notification_id, escalation_key, task_id, severity, message,
                     count, current, current),
                )
            db.commit()
            return {"notify": notify, "notification_count": count,
                    "notification_id": notification_id if notify else None,
                    "duplicate": False}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def resolve_escalation(self, lease: HeadLease, escalation_key: str, *, event_id: str,
                           now: float | None = None) -> bool:
        """Resolve an open escalation under HEAD authority."""
        current = time.time() if now is None else now
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            row = db.execute("SELECT status FROM escalations WHERE escalation_key=?",
                             (escalation_key,)).fetchone()
            if row is None:
                raise LeaseError(f"unknown escalation: {escalation_key}")
            payload = {"type": "escalation.resolved", "escalation_key": escalation_key}
            payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            if self._event_exists(db, event_id, payload_json):
                db.commit()
                return False
            if row["status"] == "open":
                db.execute("UPDATE escalations SET status='resolved', resolved_at=?, generation=? "
                           "WHERE escalation_key=?", (current, lease.generation, escalation_key))
                db.execute(
                    "UPDATE escalation_notifications SET cancelled_at=?, claim_token=NULL, "
                    "claim_expires_at=NULL WHERE escalation_key=? AND delivered_at IS NULL "
                    "AND cancelled_at IS NULL",
                    (current, escalation_key),
                )
                self._insert_event(db, event_id, lease.generation, payload_json, current)
                db.commit()
                return True
            self._insert_event(db, event_id, lease.generation, payload_json, current)
            db.commit()
            return False
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def escalations(self, *, status: str | None = None) -> list[dict]:
        if status not in {None, "open", "resolved"}:
            raise ValueError("status must be open, resolved, or null")
        with closing(self._connect()) as db:
            if status:
                rows = db.execute("SELECT * FROM escalations WHERE status=? ORDER BY opened_at",
                                  (status,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM escalations ORDER BY opened_at").fetchall()
        return [dict(row) for row in rows]

    def claim_escalation_notifications(self, lease: HeadLease, *, limit: int = 10,
                                       claim_seconds: float = 120.0,
                                       now: float | None = None) -> tuple[dict, ...]:
        """Lease-claim due notification outbox rows for at-least-once delivery."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("notification claim limit must be positive")
        if (isinstance(claim_seconds, bool) or not isinstance(claim_seconds, (int, float))
                or not math.isfinite(claim_seconds) or claim_seconds <= 0):
            raise ValueError("claim_seconds must be finite and positive")
        current = time.time() if now is None else now
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            rows = db.execute(
                "SELECT * FROM escalation_notifications WHERE delivered_at IS NULL "
                "AND cancelled_at IS NULL AND available_at<=? "
                "AND (claim_expires_at IS NULL OR claim_expires_at<=?) "
                "ORDER BY created_at, notification_id LIMIT ?",
                (current, current, limit),
            ).fetchall()
            claimed: list[dict] = []
            for row in rows:
                attempt = row["attempt_count"] + 1
                claim_token = secrets.token_urlsafe(24)
                expires = current + claim_seconds
                db.execute(
                    "UPDATE escalation_notifications SET attempt_count=?, claim_token=?, "
                    "claim_expires_at=? WHERE notification_id=?",
                    (attempt, claim_token, expires, row["notification_id"]),
                )
                event_id = f"notification-claimed:{row['notification_id']}:{attempt}"
                payload = {"type": "escalation.notification_claimed",
                           "notification_id": row["notification_id"], "attempt": attempt,
                           "claim_expires_at": expires}
                self._insert_event(db, event_id, lease.generation,
                                   json.dumps(payload, sort_keys=True, separators=(",", ":")),
                                   current)
                item = dict(row)
                item.update(attempt_count=attempt, claim_token=claim_token,
                            claim_expires_at=expires)
                claimed.append(item)
            self._assert_current(db, lease, time.time() if now is None else current)
            db.commit()
            return tuple(claimed)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def acknowledge_escalation_notification(
            self, lease: HeadLease, notification_id: str, claim_token: str, *,
            delivered: bool, event_id: str, error_code: str | None = None,
            retry_after_seconds: float = 60.0,
            now: float | None = None) -> bool:
        """Acknowledge one transport result; failures become retryable outbox rows."""
        if not isinstance(delivered, bool):
            raise ValueError("delivered must be boolean")
        if not isinstance(notification_id, str) or not notification_id.strip():
            raise ValueError("notification_id is required")
        if not isinstance(claim_token, str) or not claim_token.strip():
            raise ValueError("claim_token is required")
        if (isinstance(retry_after_seconds, bool)
                or not isinstance(retry_after_seconds, (int, float))
                or not math.isfinite(retry_after_seconds) or retry_after_seconds < 0):
            raise ValueError("retry_after_seconds must be finite and non-negative")
        if delivered:
            if error_code is not None:
                raise ValueError("successful delivery cannot include an error code")
        elif (not isinstance(error_code, str)
              or not re.fullmatch(r"[A-Z0-9_]{1,32}", error_code)):
            raise ValueError("failed delivery requires a safe error code")
        current = time.time() if now is None else now
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            payload = {"type": "escalation.notification_acknowledged",
                       "notification_id": notification_id, "delivered": delivered,
                       "error_code": error_code}
            payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            prior = db.execute("SELECT payload_json FROM state_events WHERE event_id=?",
                               (event_id,)).fetchone()
            if prior is not None:
                if prior["payload_json"] != payload_json:
                    raise LeaseError("notification acknowledgement event ID was reused")
                db.commit()
                return False
            row = db.execute("SELECT * FROM escalation_notifications WHERE notification_id=?",
                             (notification_id,)).fetchone()
            if row is None:
                raise LeaseError("unknown escalation notification")
            if row["delivered_at"] is not None:
                raise LeaseError("notification was already delivered")
            if row["cancelled_at"] is not None:
                raise LeaseError("notification was cancelled")
            if (row["claim_token"] != claim_token or row["claim_expires_at"] is None
                    or row["claim_expires_at"] <= current):
                raise LeaseError("notification claim is stale or no longer current")
            if delivered:
                db.execute(
                    "UPDATE escalation_notifications SET delivered_at=?, claim_token=NULL, "
                    "claim_expires_at=NULL, last_error_code=NULL WHERE notification_id=?",
                    (current, notification_id),
                )
            else:
                db.execute(
                    "UPDATE escalation_notifications SET available_at=?, claim_token=NULL, "
                    "claim_expires_at=NULL, last_error_code=? WHERE notification_id=?",
                    (current + retry_after_seconds, error_code, notification_id),
                )
            self._insert_event(db, event_id, lease.generation, payload_json, current)
            self._assert_current(db, lease, time.time() if now is None else current)
            db.commit()
            return delivered
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def pending_escalation_notifications(self, *, limit: int = 100) -> tuple[dict, ...]:
        """Inspect notification outbox metadata without claim tokens or secrets."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("notification limit must be positive")
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT notification_id, escalation_key, task_id, severity, message, "
                "notification_count, created_at, available_at, attempt_count, "
                "last_error_code, delivered_at, cancelled_at "
                "FROM escalation_notifications WHERE delivered_at IS NULL "
                "AND cancelled_at IS NULL ORDER BY created_at, notification_id LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def evaluate_stagnation(self, lease: HeadLease, task_id: str, sample: StagnationSample, *,
                            event_id: str, policy: dict | None = None,
                            now: float | None = None) -> dict:
        """Persist one task-health sample and return a bounded remedial action proposal."""
        current = time.time() if now is None else now
        request = {"task_id": task_id, "changed": sample.changed, "denials": sample.denials,
                   "policy": policy or {}}
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            prior_event = db.execute("SELECT payload_json FROM state_events WHERE event_id=?",
                                     (event_id,)).fetchone()
            if prior_event:
                recorded = json.loads(prior_event["payload_json"])
                if recorded.get("request") != request:
                    raise LeaseError("event_id was already used for a different health sample")
                db.commit()
                return recorded["result"]
            task_row = db.execute("SELECT payload_json FROM tasks WHERE task_id=?",
                                  (task_id,)).fetchone()
            if task_row is None:
                raise LeaseError(f"unknown task: {task_id}")
            task = TaskRecord.from_dict(json.loads(task_row["payload_json"]))
            if task.state not in {TaskState.CLAIMED, TaskState.IN_PROGRESS}:
                raise LeaseError("health samples apply only to claimed or in_progress tasks")
            row = db.execute("SELECT streak, reset_count FROM task_health WHERE task_id=?",
                             (task_id,)).fetchone()
            prior_streak = row["streak"] if row else 0
            resets = row["reset_count"] if row else 0
            streak = update_streak(prior_streak, sample)
            action = "none"
            if is_stagnant(streak, sample, policy):
                action = remedial_kind(resets, policy)
                if action == "redispatch":
                    running_invocation = db.execute(
                        "SELECT invocation_id FROM task_invocation_liveness "
                        "WHERE task_id=? AND state='running' LIMIT 1", (task_id,),
                    ).fetchone()
                    if running_invocation is not None:
                        # A progress signal cannot authorize a second maker while
                        # the prior process has no terminal receipt/cancellation proof.
                        action = "hold_running"
                    else:
                        resets += 1
                streak = 0
            result = {"task_id": task_id, "streak": streak, "reset_count": resets,
                      "action": action}
            payload = {"type": "task.health_sampled", "request": request, "result": result}
            payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            db.execute("INSERT INTO task_health VALUES(?, ?, ?, ?, ?, ?, ?) "
                       "ON CONFLICT(task_id) DO UPDATE SET streak=excluded.streak, "
                       "reset_count=excluded.reset_count, last_changed=excluded.last_changed, "
                       "last_denials=excluded.last_denials, updated_at=excluded.updated_at, "
                       "generation=excluded.generation",
                       (task_id, streak, resets, int(sample.changed), sample.denials,
                        current, lease.generation))
            self._insert_event(db, event_id, lease.generation, payload_json, current)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def heartbeat_task_invocation(self, lease: HeadLease, task_id: str,
                                  invocation_id: str, *,
                                  now: float | None = None) -> None:
        """Refresh liveness for a running maker while its HEAD guard renews."""
        current = time.time() if now is None else now
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            row = db.execute(
                "SELECT task_id, state FROM task_invocation_liveness WHERE invocation_id=?",
                (invocation_id,),
            ).fetchone()
            if row is None or row["task_id"] != task_id or row["state"] != "running":
                raise LeaseError("maker invocation liveness record is not running")
            db.execute(
                "UPDATE task_invocation_liveness SET heartbeat_at=?, generation=? "
                "WHERE invocation_id=?",
                (current, lease.generation, invocation_id),
            )
            self._assert_current(db, lease, time.time() if now is None else current)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def record_task_invocation_process(self, lease: HeadLease, task_id: str,
                                       invocation_id: str, identity: ProcessIdentity, *,
                                       now: float | None = None) -> bool:
        """Bind an active maker heartbeat to its OS process under the HEAD lease."""
        if (not isinstance(task_id, str) or not task_id.strip()
                or not isinstance(invocation_id, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", invocation_id)
                or not isinstance(identity, ProcessIdentity)):
            raise ValueError("maker process identity inputs are invalid")
        current = time.time() if now is None else now
        payload = {
            "type": "task.invocation_process_started", "task_id": task_id,
            "invocation_id": invocation_id, "pid": identity.pid,
            "start_token": identity.start_token,
            "process_group_id": identity.process_group_id,
            "containment_ref_sha256": (
                hashlib.sha256(identity.containment_ref.encode("utf-8")).hexdigest()
                if identity.containment_ref else None
            ),
        }
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        event_id = "maker-process:" + invocation_id
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            row = db.execute(
                "SELECT task_id, state, process_pid, process_start_token, process_group_id, "
                "process_containment_ref "
                "FROM task_invocation_liveness WHERE invocation_id=?", (invocation_id,),
            ).fetchone()
            if row is None or row["task_id"] != task_id or row["state"] != "running":
                raise LeaseError("maker process identity requires running invocation liveness")
            existing = (row["process_pid"], row["process_start_token"],
                        row["process_group_id"], row["process_containment_ref"])
            requested = (identity.pid, identity.start_token, identity.process_group_id,
                         identity.containment_ref)
            if row["process_pid"] is not None:
                if existing != requested:
                    raise LeaseError("maker invocation process identity cannot be replaced")
                if not self._event_exists(db, event_id, payload_json):
                    raise LeaseError("maker process identity event is missing or mismatched")
                db.commit()
                return False
            if self._event_exists(db, event_id, payload_json):
                raise LeaseError("maker process event exists without its liveness identity")
            db.execute(
                "UPDATE task_invocation_liveness SET process_pid=?, process_start_token=?, "
                "process_group_id=?, process_containment_ref=?, heartbeat_at=?, generation=? "
                "WHERE invocation_id=?",
                (identity.pid, identity.start_token, identity.process_group_id,
                 identity.containment_ref,
                 current, lease.generation, invocation_id),
            )
            self._insert_event(db, event_id, lease.generation, payload_json, current)
            self._assert_current(db, lease, current)
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def task_invocation_liveness(self, *, task_id: str | None = None,
                                state: str | None = None) -> list[dict]:
        """Read maker liveness metadata for health sampling and diagnostics."""
        if task_id is not None and (not isinstance(task_id, str) or not task_id.strip()):
            raise ValueError("task_id must be null or non-empty")
        if state not in {None, "running", "completed"}:
            raise ValueError("state must be running, completed, or null")
        clauses, values = [], []
        if task_id is not None:
            clauses.append("task_id=?")
            values.append(task_id)
        if state is not None:
            clauses.append("state=?")
            values.append(state)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT task_id, invocation_id, state, started_at, heartbeat_at, generation, "
                "process_pid, process_start_token, process_group_id, process_containment_ref "
                "FROM task_invocation_liveness" + where +
                " ORDER BY started_at, invocation_id", values,
            ).fetchall()
        return [dict(row) for row in rows]

    def reap_stale_invocation(self, lease: HeadLease, invocation_id: str, *,
                              stale_after_seconds: float,
                              grace_seconds: float = 0.25,
                              timeout_seconds: float = 5.0,
                              now: float | None = None) -> ProcessReapResult:
        """Explicitly reap one stale maker under an extended HEAD lease.

        The SQLite writer lock fences competing HEAD acquisition while the
        bounded host effect runs. Only a backend that proves the complete
        process tree marks liveness complete. Verified process-group
        termination is useful evidence but cannot prove detached descendants
        are absent, so it remains running. Unsupported and ambiguous outcomes
        are also recorded. This operation is never called implicitly by
        dispatch cycles.
        """
        if (not isinstance(invocation_id, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", invocation_id)
                or isinstance(stale_after_seconds, bool)
                or not isinstance(stale_after_seconds, (int, float))
                or not math.isfinite(stale_after_seconds) or stale_after_seconds < 0
                or isinstance(grace_seconds, bool)
                or not isinstance(grace_seconds, (int, float))
                or not math.isfinite(grace_seconds) or not 0 <= grace_seconds <= 5
                or isinstance(timeout_seconds, bool)
                or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds)
                or not 0.1 <= timeout_seconds <= 30):
            raise ValueError("stale invocation reaper inputs are invalid")
        current = time.time() if now is None else now
        renewed_lease = self.renew_head(
            lease, ttl_seconds=(2 * float(timeout_seconds) + float(grace_seconds) + 15),
            now=current,
        )
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, renewed_lease, current)
            row = db.execute(
                "SELECT task_id, state, heartbeat_at, process_pid, process_start_token, "
                "process_group_id, process_containment_ref "
                "FROM task_invocation_liveness WHERE invocation_id=?",
                (invocation_id,),
            ).fetchone()
            if row is None or row["state"] != "running":
                raise LeaseError("only a running maker invocation can be reaped")
            if row["heartbeat_at"] + stale_after_seconds > current:
                raise LeaseError("maker invocation is not stale enough to reap")
            if row["process_pid"] is None:
                raise LeaseError("maker invocation has no durable process identity")
            try:
                identity = ProcessIdentity(row["process_pid"], row["process_start_token"],
                                           row["process_group_id"],
                                           row["process_containment_ref"])
            except (TypeError, ValueError) as exc:
                raise LeaseError("maker invocation process identity is malformed") from exc

            result = reap_managed_process(
                identity, grace_seconds=float(grace_seconds),
                timeout_seconds=float(timeout_seconds),
            )
            observed_at = current if now is not None else time.time()
            self._assert_current(db, renewed_lease, observed_at)
            identity_hash = hashlib.sha256(identity.start_token.encode("utf-8")).hexdigest()
            payload = {
                "type": ("task.invocation_process_reaped" if result.whole_tree_verified
                         else ("task.invocation_process_group_terminated" if result.verified
                               else "task.invocation_reap_attempted")),
                "task_id": row["task_id"], "invocation_id": invocation_id,
                "process_pid": identity.pid,
                "process_group_id": identity.process_group_id,
                "identity_fingerprint_sha256": identity_hash,
                "method": result.method, "status": result.status.value,
                "verified": result.verified,
                "whole_tree_verified": result.whole_tree_verified,
            }
            payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            result_fingerprint = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()[:16]
            event_id = (f"invocation-reaper:{invocation_id}:{identity_hash[:16]}:"
                        f"{result_fingerprint}")
            if result.whole_tree_verified:
                db.execute(
                    "UPDATE task_invocation_liveness SET state='completed', heartbeat_at=?, "
                    "generation=? WHERE invocation_id=? AND state='running'",
                    (observed_at, renewed_lease.generation, invocation_id),
                )
            self._insert_event(
                db, event_id, renewed_lease.generation, payload_json, observed_at,
            )
            self._assert_current(db, renewed_lease, observed_at)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def recover_invocation_cancellation_receipts(self, lease: HeadLease, *,
                                                now: float | None = None) -> tuple[str, ...]:
        """Verify adapter-signed cancellation receipts and release held maker liveness."""
        current = time.time() if now is None else now
        root = self.cancellation_receipt_directory
        if not root.exists() and not root.is_symlink():
            return ()
        if root.is_symlink() or not root.is_dir():
            raise LeaseError("cancellation receipt directory must be a real directory")
        root = root.resolve(strict=True)
        recovered: list[str] = []
        for path in sorted(root.glob("*.json")):
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
                continue
            try:
                raw = path.read_bytes()
                receipt = json.loads(raw.decode("utf-8"))
            except (OSError, UnicodeError, ValueError):
                continue
            if (not isinstance(receipt, dict) or set(receipt) != {"payload", "signature"}
                    or not isinstance(receipt.get("payload"), dict)
                    or not isinstance(receipt.get("signature"), str)):
                continue
            body = receipt["payload"]
            required = {"version", "invocation_id", "task_id", "method",
                        "verified", "exit_code", "observed_at"}
            if (set(body) != required or body.get("version") != 1
                    or body.get("verified") is not True
                    or body.get("method") not in {
                        "windows_taskkill_tree", "posix_process_group"
                    }):
                continue
            invocation_id = body.get("invocation_id")
            task_id = body.get("task_id")
            observed_at = body.get("observed_at")
            exit_code = body.get("exit_code")
            if (not isinstance(invocation_id, str)
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", invocation_id)
                    or path.name != hashlib.sha256(invocation_id.encode("utf-8")).hexdigest() + ".json"
                    or not isinstance(task_id, str) or not task_id.strip()
                    or not isinstance(observed_at, (int, float))
                    or isinstance(observed_at, bool) or not math.isfinite(observed_at)
                    or observed_at < 0
                    or (exit_code is not None and
                        (not isinstance(exit_code, int) or isinstance(exit_code, bool)))):
                continue
            canonical_body = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
            receipt_sha256 = hashlib.sha256(raw).hexdigest()
            event_id = "invocation-cancellation:" + invocation_id
            db = self._connect()
            try:
                db.execute("BEGIN IMMEDIATE")
                self._assert_current(db, lease, current)
                live = db.execute(
                    "SELECT task_id, state, termination_hmac_key "
                    "FROM task_invocation_liveness WHERE invocation_id=?",
                    (invocation_id,),
                ).fetchone()
                if (live is None or live["task_id"] != task_id
                        or live["termination_hmac_key"] is None
                        or live["state"] not in {"running", "completed"}):
                    db.rollback()
                    continue
                expected_signature = hmac.new(
                    live["termination_hmac_key"].encode("utf-8"),
                    canonical_body, hashlib.sha256,
                ).hexdigest()
                if not hmac.compare_digest(receipt["signature"], expected_signature):
                    db.rollback()
                    continue
                payload = {
                    "type": "runtime.cancellation_verified",
                    "invocation_id": invocation_id,
                    "task_id": task_id,
                    "method": body["method"],
                    "exit_code": exit_code,
                    "observed_at": float(observed_at),
                    "receipt_sha256": receipt_sha256,
                    "evidence_source": "adapter_sidecar",
                }
                payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
                existing_event = db.execute(
                    "SELECT payload_json FROM state_events WHERE event_id=?", (event_id,)
                ).fetchone()
                if existing_event is not None:
                    if existing_event["payload_json"] != payload_json:
                        raise LeaseError("cancellation receipt conflicts with prior evidence")
                    db.commit()
                    recovered.append(invocation_id)
                else:
                    db.execute(
                        "INSERT INTO invocation_cancellation_receipts "
                        "(invocation_id, task_id, method, exit_code, observed_at, receipt_sha256, "
                        "evidence_source, generation) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                        (invocation_id, task_id, body["method"], exit_code,
                         float(observed_at), receipt_sha256, "adapter_sidecar", lease.generation),
                    )
                    db.execute(
                        "UPDATE task_invocation_liveness SET state='completed', heartbeat_at=?, "
                        "generation=? WHERE invocation_id=? AND state='running'",
                        (current, lease.generation, invocation_id),
                    )
                    self._insert_event(db, event_id, lease.generation, payload_json, current)
                    db.commit()
                    recovered.append(invocation_id)
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        return tuple(recovered)

    def seed_task(self, lease: HeadLease, task: TaskRecord, *, event_id: str,
                  now: float | None = None) -> bool:
        """Import a task under the active HEAD lease; duplicate IDs are refused."""
        current = time.time() if now is None else now
        payload = {"type": "task.seeded", "task": task.to_dict()}
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            if self._event_exists(db, event_id, payload_json):
                db.commit()
                return False
            exists = db.execute("SELECT 1 FROM tasks WHERE task_id=?", (task.task_id,)).fetchone()
            if exists:
                raise LeaseError(f"task already exists: {task.task_id}")
            db.execute("INSERT INTO tasks VALUES(?, ?, ?)", (task.task_id,
                       json.dumps(task.to_dict(), sort_keys=True, separators=(",", ":")), current))
            self._insert_event(db, event_id, lease.generation, payload_json, current)
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def import_handover_tasks(self, lease: HeadLease, tasks: tuple[TaskRecord, ...],
                              historical_task_ids: tuple[str, ...], *,
                              context_fields: tuple[tuple[str, str, int, str], ...] = (),
                              source_plan_sha256: str, mapping_sha256: str,
                              event_id: str, now: float | None = None) -> bool:
        """Atomically stage a validated translation in an empty target store.

        This records provenance and historical dependency IDs but does not
        activate a HEAD or claim that the source process was fenced.
        """
        if not isinstance(tasks, tuple) or not all(isinstance(task, TaskRecord) for task in tasks):
            raise ValueError("handover tasks must be a tuple of TaskRecord values")
        if (not isinstance(historical_task_ids, tuple)
                or not all(isinstance(item, str) and item.strip() for item in historical_task_ids)):
            raise ValueError("historical task IDs must be a tuple of non-empty strings")
        for name, value in (("source PLAN", source_plan_sha256), ("mapping", mapping_sha256)):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"{name} hash must be a lowercase SHA-256 digest")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("handover event_id must be non-empty")
        if (not isinstance(context_fields, tuple)
                or not all(isinstance(item, tuple) and len(item) == 4 for item in context_fields)):
            raise ValueError("handover context fields must be (task_id, field_name, occurrence, value) tuples")
        imported_ids = [task.task_id for task in tasks]
        historical = set(historical_task_ids)
        if len(imported_ids) != len(set(imported_ids)) or len(historical) != len(historical_task_ids):
            raise ValueError("handover task IDs must be unique")
        if set(imported_ids) & historical:
            raise ValueError("historical and open handover task IDs must be disjoint")
        if any(task.state not in {TaskState.PENDING, TaskState.BLOCKED} for task in tasks):
            raise ValueError("handover may stage only pending or blocked tasks")
        findings = validate_task_set(tasks, archived_task_ids=historical_task_ids)
        if findings:
            raise LeaseError("handover task set is invalid: " + "; ".join(findings))
        known_ids = set(imported_ids) | historical
        unresolved = sorted({dependency for task in tasks for dependency in task.depends_on
                             if dependency not in known_ids})
        if unresolved:
            raise LeaseError("handover dependencies are unresolved: " + ", ".join(unresolved))
        context_keys: set[tuple[str, str, int]] = set()
        for item in context_fields:
            task_id, field_name, occurrence, value = item
            if (not isinstance(task_id, str) or task_id not in known_ids
                    or not isinstance(field_name, str) or not re.fullmatch(r"[A-Za-z_]+", field_name)
                    or not isinstance(occurrence, int) or isinstance(occurrence, bool) or occurrence < 1
                    or not isinstance(value, str)):
                raise ValueError("handover context field entry is malformed")
            key = (task_id, field_name, occurrence)
            if key in context_keys:
                raise ValueError("handover context field occurrences must be unique")
            context_keys.add(key)
            if find_secrets(value):
                raise LeaseError(f"secret-like content in handover context field {field_name} was refused")
        context_fields = tuple(sorted(context_fields, key=lambda item: (item[0], item[1], item[2])))
        context_digest = hashlib.sha256(json.dumps(
            context_fields, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()

        current = time.time() if now is None else now
        payload = {
            "type": "handover.tasks_staged", "source_plan_sha256": source_plan_sha256,
            "mapping_sha256": mapping_sha256, "task_ids": sorted(imported_ids),
            "historical_task_ids": sorted(historical), "activation_authorized": False,
            "context_field_count": len(context_fields),
            "context_fields_sha256": context_digest,
        }
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            mode = db.execute("SELECT mode FROM supervisor_mode WHERE singleton=1").fetchone()
            if mode is not None and mode["mode"] != "parked":
                raise LeaseError("handover import requires the target supervisor to be parked")
            prior = db.execute("SELECT payload_json FROM state_events WHERE event_id=?",
                               (event_id,)).fetchone()
            if prior is not None:
                if prior["payload_json"] != payload_json:
                    raise LeaseError("handover event ID was already used for different data")
                db.commit()
                return False
            occupied = db.execute("SELECT task_id FROM tasks UNION SELECT task_id FROM task_archive "
                                  "UNION SELECT task_id FROM historical_task_ids "
                                  "UNION SELECT event_id FROM handover_imports LIMIT 1").fetchone()
            if occupied is not None:
                raise LeaseError("handover import requires an empty target task store")
            db.execute("INSERT INTO handover_imports VALUES(?, ?, ?, ?, ?)",
                       (event_id, source_plan_sha256, mapping_sha256, current, lease.generation))
            for task in tasks:
                db.execute("INSERT INTO tasks VALUES(?, ?, ?)",
                           (task.task_id, json.dumps(task.to_dict(), sort_keys=True,
                                                     separators=(",", ":")), current))
            for task_id in sorted(historical):
                db.execute("INSERT INTO historical_task_ids VALUES(?, ?, ?, ?)",
                           (task_id, source_plan_sha256, current, lease.generation))
            for task_id, field_name, occurrence, value in context_fields:
                db.execute("INSERT INTO handover_context_fields VALUES(?, ?, ?, ?, ?, ?)",
                           (task_id, field_name, occurrence, value,
                            source_plan_sha256, lease.generation))
            self._insert_event(db, event_id, lease.generation, payload_json, current)
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def historical_task_ids(self) -> tuple[str, ...]:
        """Return source-completed dependency IDs without treating them as reviewed DONE tasks."""
        with closing(self._connect()) as db:
            rows = db.execute("SELECT task_id FROM historical_task_ids ORDER BY task_id").fetchall()
        return tuple(row["task_id"] for row in rows)

    def handover_context_fields(self, task_id: str | None = None) -> tuple[dict, ...]:
        """Read explicitly preserved legacy fields without projecting them into task authority."""
        if task_id is not None and (not isinstance(task_id, str) or not task_id.strip()):
            raise ValueError("task_id must be null or non-empty")
        with closing(self._connect()) as db:
            imports = db.execute(
                "SELECT event_id, source_plan_sha256 FROM handover_imports ORDER BY imported_at, event_id"
            ).fetchall()
            rows = db.execute(
                "SELECT task_id, field_name, occurrence, field_value, source_plan_sha256 "
                "FROM handover_context_fields ORDER BY task_id, field_name, occurrence"
            ).fetchall()
            if not imports:
                if rows:
                    raise LeaseError("handover context exists without import provenance")
                return ()
            if len(imports) != 1:
                raise LeaseError("target store contains ambiguous handover provenance")
            event = db.execute("SELECT payload_json FROM state_events WHERE event_id=?",
                               (imports[0]["event_id"],)).fetchone()
            if event is None:
                raise LeaseError("handover import event is missing")
            try:
                payload = json.loads(event["payload_json"])
            except (TypeError, ValueError) as exc:
                raise LeaseError("handover import event is malformed") from exc
            context = tuple((row["task_id"], row["field_name"], row["occurrence"],
                             row["field_value"]) for row in rows)
            digest = hashlib.sha256(json.dumps(
                context, ensure_ascii=False, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            if (payload.get("type") != "handover.tasks_staged"
                    or payload.get("source_plan_sha256") != imports[0]["source_plan_sha256"]
                    or payload.get("context_field_count") != len(context)
                    or payload.get("context_fields_sha256") != digest
                    or any(row["source_plan_sha256"] != imports[0]["source_plan_sha256"]
                           for row in rows)):
                raise LeaseError("preserved handover context failed provenance verification")
        selected = [dict(row) for row in rows if task_id is None or row["task_id"] == task_id]
        return tuple(selected)

    def completed_dependency_ids(self) -> frozenset[str]:
        """Return reviewed DONE tasks and historical source dependencies separately stored."""
        completed = {task.task_id for task in self.list_tasks() if task.state is TaskState.DONE}
        return frozenset(completed | set(self.historical_task_ids()))

    def archive_completed_task(self, lease: HeadLease, task_id: str, *, event_id: str,
                               now: float | None = None) -> bool:
        """Preserve a completed task's exact protocol payload in an idempotent archive."""
        current = time.time() if now is None else now
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            task_row = db.execute("SELECT payload_json FROM tasks WHERE task_id=?",
                                  (task_id,)).fetchone()
            if task_row is None:
                raise LeaseError(f"unknown task: {task_id}")
            task = TaskRecord.from_dict(json.loads(task_row["payload_json"]))
            if task.state is not TaskState.DONE:
                raise LeaseError("only completed tasks can be archived")
            payload_json = json.dumps(task.to_dict(), sort_keys=True, separators=(",", ":"))
            payload_sha = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
            payload = {"type": "task.archived", "task_id": task_id,
                       "payload_sha256": payload_sha}
            event_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            if self._event_exists(db, event_id, event_json):
                db.commit()
                return False
            existing = db.execute("SELECT payload_sha256 FROM task_archive WHERE task_id=?",
                                  (task_id,)).fetchone()
            if existing:
                if existing["payload_sha256"] != payload_sha:
                    raise LeaseError("archived task payload does not match current task")
                self._insert_event(db, event_id, lease.generation, event_json, current)
                db.commit()
                return False
            db.execute("INSERT INTO task_archive VALUES(?, ?, ?, ?, ?)",
                       (task_id, payload_json, payload_sha, current, lease.generation))
            self._insert_event(db, event_id, lease.generation, event_json, current)
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def archive_older_plan_tasks(self, lease: HeadLease, project_root: str | Path, *,
                                 event_id: str, expected_plan_sha256: str,
                                 now: float | None = None) -> tuple[str, ...]:
        """Archive authoritative completed older-wave tasks and project PLAN atomically.

        Monthly archive blocks are written idempotently before committing the
        PLAN projection intent. A crash at that boundary can leave verified,
        harmless archive blocks; retry verifies those exact blocks and resumes.
        """
        from .plan_archive import ArchiveConflict

        root, plan_path = _resolve_plan_path(project_root)
        self.apply_pending_plan_projections(lease, now=now)
        with closing(self._connect()) as prior_db:
            prior_row = prior_db.execute(
                "SELECT payload_json FROM state_events WHERE event_id=?", (event_id,)
            ).fetchone()
        if prior_row is not None:
            prior_payload = json.loads(prior_row["payload_json"])
            if (prior_payload.get("type") != "plan.tasks_archived"
                    or prior_payload.get("project_root") != str(root)):
                raise LeaseError("event_id was already used for a different operation")
            return tuple(prior_payload.get("task_ids", ()))
        try:
            source_bytes = plan_path.read_bytes()
            source_text = source_bytes.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise PlanWriteConflict(f"could not read PLAN.md: {exc}") from exc
        source_sha = hashlib.sha256(source_bytes).hexdigest()
        if (not isinstance(expected_plan_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_plan_sha256)):
            raise ValueError("expected_plan_sha256 must be a lowercase SHA-256 digest")
        if source_sha != expected_plan_sha256:
            raise PlanWriteConflict("PLAN changed since it was read")
        result = plan_archive(source_text)
        if result.findings:
            raise ArchiveConflict("; ".join(result.findings))
        if not result.blocks:
            return ()
        projected_bytes = result.text.encode("utf-8")
        projected_sha = hashlib.sha256(projected_bytes).hexdigest()
        current = time.time() if now is None else now
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            prior = db.execute("SELECT payload_json FROM state_events WHERE event_id=?",
                               (event_id,)).fetchone()
            if prior is not None:
                prior_payload = json.loads(prior["payload_json"])
                ids = tuple(sorted(block.task_id for block in result.blocks))
                if (prior_payload.get("type") != "plan.tasks_archived"
                        or tuple(prior_payload.get("task_ids", ())) != ids
                        or prior_payload.get("projected_sha256") != projected_sha
                        or prior_payload.get("project_root") != str(root)):
                    raise LeaseError("event_id was already used for a different PLAN archive")
                db.commit()
                try:
                    self.apply_pending_plan_projections(lease, now=now)
                except Exception as exc:
                    raise PlanProjectionPending(event_id, str(exc)) from exc
                return ids
            pending = db.execute(
                "SELECT event_id FROM plan_projection_outbox WHERE project_root=? LIMIT 1",
                (str(root),),
            ).fetchone()
            if pending is not None:
                raise LeaseError(
                    f"PLAN projection {pending['event_id']} must be recovered before another write"
                )
            # Re-read while holding the database writer lock. This prevents
            # lease takeover from interleaving with verified archive writes.
            try:
                locked_source = plan_path.read_bytes()
            except OSError as exc:
                raise PlanWriteConflict(f"could not re-read PLAN.md: {exc}") from exc
            if hashlib.sha256(locked_source).hexdigest() != source_sha:
                raise PlanWriteConflict("PLAN changed before archive projection")
            ids = tuple(sorted(block.task_id for block in result.blocks))
            tasks: dict[str, tuple[str, str]] = {}
            for task_id in ids:
                row = db.execute("SELECT payload_json FROM tasks WHERE task_id=?",
                                 (task_id,)).fetchone()
                if row is None:
                    raise LeaseError(f"archive candidate is missing from authoritative state: {task_id}")
                task = TaskRecord.from_dict(json.loads(row["payload_json"]))
                if task.state is not TaskState.DONE:
                    raise LeaseError(f"archive candidate is not authoritatively done: {task_id}")
                payload_json = json.dumps(task.to_dict(), sort_keys=True, separators=(",", ":"))
                tasks[task_id] = (payload_json, hashlib.sha256(payload_json.encode("utf-8")).hexdigest())
            append_archive_blocks(root, result)
            for task_id, (payload_json, payload_sha) in tasks.items():
                existing = db.execute("SELECT payload_sha256 FROM task_archive WHERE task_id=?",
                                      (task_id,)).fetchone()
                if existing is not None and existing["payload_sha256"] != payload_sha:
                    raise LeaseError(f"archived task payload does not match current task: {task_id}")
                if existing is None:
                    db.execute("INSERT INTO task_archive VALUES(?, ?, ?, ?, ?)",
                               (task_id, payload_json, payload_sha, current, lease.generation))
            payload = {"type": "plan.tasks_archived", "project_root": str(root),
                       "task_ids": list(ids),
                       "projected_sha256": projected_sha}
            payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            self._insert_event(db, event_id, lease.generation, payload_json, current)
            if source_sha != projected_sha:
                db.execute(
                    "INSERT INTO plan_projection_outbox VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (event_id, str(root), source_sha, projected_sha,
                     projected_bytes, current, lease.generation),
                )
            # Archive writes touch a second filesystem artifact while the DB
            # writer lock is held; recheck real lease expiry before committing.
            self._assert_current(db, lease, time.time() if now is None else current)
            db.commit()
            try:
                self.apply_pending_plan_projections(lease, now=now)
            except Exception as exc:
                raise PlanProjectionPending(event_id, str(exc)) from exc
            return ids
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def archived_tasks(self) -> list[TaskRecord]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT payload_json FROM task_archive ORDER BY archived_at, task_id").fetchall()
        return [TaskRecord.from_dict(json.loads(row["payload_json"])) for row in rows]

    def assign_pending_task(self, lease: HeadLease, task_id: str, worker_id: str,
                            registry: WorkerRegistry, *, event_id: str,
                            machine_id: str | None = None, require_strict: bool = False,
                            require_supervisor_running: bool = False,
                            project_root: str | Path | None = None,
                            now: float | None = None) -> TaskRecord:
        """Claim and identity-snapshot an eligible task in one leased transaction."""
        self.apply_pending_plan_projections(lease, now=now)
        worker = registry.resolve(worker_id)
        if worker_id not in registry.active:
            raise LeaseError("only active workers can be assigned tasks")
        if require_strict and worker.control_mode != "strict":
            raise LeaseError("strict control requires a strictly verified worker")
        if machine_id and worker.machine_affinity and machine_id not in worker.machine_affinity:
            raise LeaseError("worker is not allowed on this machine")
        current = time.time() if now is None else now
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            if require_supervisor_running:
                supervisor = db.execute("SELECT mode FROM supervisor_mode WHERE singleton=1").fetchone()
                if supervisor is None or supervisor["mode"] != "running":
                    raise LeaseError("dispatch requires supervisor running mode")
            prior_event = db.execute("SELECT payload_json FROM state_events WHERE event_id=?",
                                     (event_id,)).fetchone()
            if prior_event:
                recorded = json.loads(prior_event["payload_json"])
                prior_task = TaskRecord.from_dict(recorded["task"])
                if (recorded.get("type") != "task.assigned" or prior_task.task_id != task_id
                        or prior_task.assigned_worker != worker_id):
                    raise LeaseError("event_id was already used for a different assignment")
                db.commit()
                return prior_task
            task_row = db.execute("SELECT payload_json FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if task_row is None:
                raise LeaseError(f"unknown task: {task_id}")
            task = TaskRecord.from_dict(json.loads(task_row["payload_json"]))
            if task.maker_identity and task.maker_identity != {
                "unit_id": worker.identity.unit_id, "runtime": worker.identity.runtime,
                "model": worker.identity.model
            }:
                raise LeaseError("pending task already has a different maker identity snapshot")
            assigned = make_assignment(task, worker, machine_id=machine_id,
                                       require_strict=require_strict)
            others = [TaskRecord.from_dict(json.loads(row[0])) for row in
                      db.execute("SELECT payload_json FROM tasks WHERE task_id<>?", (task_id,)).fetchall()]
            active_states = {TaskState.CLAIMED, TaskState.IN_PROGRESS, TaskState.NEEDS_REVIEW}
            if any(item.assigned_worker == worker_id and item.state in active_states for item in others):
                raise LeaseError("worker already has an active task")
            completed_dependencies = {item.task_id for item in others
                                      if item.state is TaskState.DONE}
            completed_dependencies.update(
                row[0] for row in db.execute("SELECT task_id FROM historical_task_ids").fetchall()
            )
            if any(dep not in completed_dependencies for dep in assigned.depends_on):
                raise LeaseError("task dependencies must all be completed before assignment")
            claimed = replace(assigned, state=TaskState.CLAIMED)
            projection = (_prepare_plan_projection(
                project_root, task_id, TaskState.CLAIMED, worker_id,
            ) if project_root is not None else None)
            if projection is not None:
                pending = db.execute(
                    "SELECT event_id FROM plan_projection_outbox WHERE project_root=? LIMIT 1",
                    (projection["project_root"],),
                ).fetchone()
                if pending is not None:
                    raise LeaseError(
                        f"PLAN projection {pending['event_id']} must be recovered before another write"
                    )
            findings = validate_task_set([*others, claimed])
            overlap = [finding for finding in findings if "territory overlaps" in finding]
            if overlap:
                active_states = {TaskState.CLAIMED, TaskState.IN_PROGRESS, TaskState.NEEDS_REVIEW}
                conflict_details = []
                for other in others:
                    if other.state not in active_states:
                        continue
                    for attempted_path in claimed.owned_paths:
                        for existing_path in other.owned_paths:
                            if not _territories_intersect((attempted_path,), (existing_path,)):
                                continue
                            subject_path = _exact_conflict_subject(attempted_path, existing_path)
                            conflict_details.append({
                                "other_task_id": other.task_id,
                                "attempted_path": attempted_path,
                                "existing_path": existing_path,
                                "subject_path": subject_path,
                            })
                conflict_details = sorted(
                    {json.dumps(item, sort_keys=True): item for item in conflict_details}.values(),
                    key=lambda item: (item["other_task_id"], item["attempted_path"], item["existing_path"]),
                )
                conflict_event_id = "territory-conflict:" + event_id
                conflict_payload = {"type": "territory.conflict", "task_id": task_id,
                                    "conflicts": conflict_details}
                conflict_json = json.dumps(conflict_details, sort_keys=True, separators=(",", ":"))
                prior_conflict = db.execute(
                    "SELECT conflicts_json FROM territory_conflicts WHERE conflict_event_id=?",
                    (conflict_event_id,),
                ).fetchone()
                if prior_conflict and prior_conflict["conflicts_json"] != conflict_json:
                    raise LeaseError("territory conflict event ID was reused for different data")
                if prior_conflict is None:
                    db.execute("INSERT INTO territory_conflicts VALUES (?, ?, ?, ?, ?)",
                               (conflict_event_id, task_id, conflict_json, current, lease.generation))
                    self._insert_event(
                        db, conflict_event_id, lease.generation,
                        json.dumps(conflict_payload, sort_keys=True, separators=(",", ":")), current,
                    )
                db.commit()
                raise LeaseError("assignment territory conflicts: " + "; ".join(overlap))
            payload = {"type": "task.assigned", "task": claimed.to_dict()}
            payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            if self._event_exists(db, event_id, payload_json):
                db.commit()
                return claimed
            db.execute("UPDATE tasks SET payload_json=?, updated_at=? WHERE task_id=?",
                       (json.dumps(claimed.to_dict(), sort_keys=True, separators=(",", ":")),
                        current, task_id))
            self._insert_event(db, event_id, lease.generation, payload_json, current)
            if projection is not None and (
                projection["expected_sha256"] != projection["projected_sha256"]
            ):
                db.execute(
                    "INSERT INTO plan_projection_outbox VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (event_id, projection["project_root"],
                     projection["expected_sha256"], projection["projected_sha256"],
                     projection["projected_text"], current, lease.generation),
                )
            db.commit()
            if projection is not None:
                try:
                    self.apply_pending_plan_projections(lease, now=now)
                except Exception as exc:
                    raise PlanProjectionPending(event_id, str(exc)) from exc
            return claimed
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def register_test_run(self, lease: HeadLease, task_id: str, result: TestRunResult, *,
                          event_id: str, now: float | None = None) -> str:
        """Register passed SHA-bound test evidence to an active assigned task."""
        if not isinstance(result, TestRunResult) or not result.passed:
            raise LeaseError("only a passed TestRunResult can be registered")
        try:
            artifact_path = Path(result.artifact_path).resolve(strict=True)
            artifact_bytes = artifact_path.read_bytes()
            artifact = json.loads(artifact_bytes.decode("utf-8"))
            log_path = Path(artifact["log_path"]).resolve(strict=True)
            log_hash = hashlib.sha256(log_path.read_bytes()).hexdigest()
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
            raise LeaseError(f"test-run artifact is missing or invalid: {exc}") from exc
        artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()
        if (artifact_hash != result.artifact_sha256 or not artifact.get("passed")
                or artifact.get("sha") != result.sha or artifact.get("name") != result.name
                or artifact.get("command_sha256") != TestRunCache.command_fingerprint(result.command)
                or artifact.get("environment_fingerprint") != result.environment_fingerprint
                or artifact.get("output_sha256") != result.output_sha256
                or log_hash != result.output_sha256
                or log_path != Path(result.log_path).resolve()):
            raise LeaseError("test-run evidence does not match its immutable artifacts")
        evidence_ref = result.evidence_ref
        payload = {"type": "test_run.registered", "task_id": task_id,
                   "sha": result.sha, "test_name": result.name,
                   "evidence_ref": evidence_ref, "artifact_sha256": artifact_hash}
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        current = time.time() if now is None else now
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            task_row = db.execute("SELECT payload_json FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if task_row is None:
                raise LeaseError(f"unknown task: {task_id}")
            task = TaskRecord.from_dict(json.loads(task_row["payload_json"]))
            if task.state not in {TaskState.CLAIMED, TaskState.IN_PROGRESS,
                                  TaskState.NEEDS_REVIEW} or not task.assigned_worker:
                raise LeaseError("test evidence can only be registered to an active assigned task")
            prior = db.execute("SELECT payload_json FROM state_events WHERE event_id=?",
                               (event_id,)).fetchone()
            if prior:
                recorded = json.loads(prior["payload_json"])
                if recorded != payload:
                    raise LeaseError("event_id was already used for different test evidence")
                db.commit()
                return evidence_ref
            db.execute("INSERT OR IGNORE INTO test_run_receipts VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (task_id, evidence_ref, result.sha, result.name,
                        result.environment_fingerprint, str(artifact_path), artifact_hash,
                        result.output_sha256, current, lease.generation))
            self._insert_event(db, event_id, lease.generation, payload_json, current)
            db.commit()
            return evidence_ref
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def apply_control(self, lease: HeadLease, message: ControlMessage, *,
                      project_root: str | Path | None = None,
                      expected_plan_sha256: str | None = None,
                      now: float | None = None) -> bool:
        """Apply one builder CONTROL report under HEAD authority and receipt checks."""
        current = time.time() if now is None else now
        if project_root is None and expected_plan_sha256 is not None:
            raise ValueError("expected_plan_sha256 requires project_root")
        self.apply_pending_plan_projections(lease, now=now)
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            payload = {"type": "control.applied", "event_id": message.event_id,
                       "task_id": message.task_id, "worker_id": message.worker_id,
                       "requested_state": message.requested_state.value
                       if message.requested_state else None,
                       "head_sha": message.head_sha,
                       "progress_note": message.progress_note,
                       "blocked_reason": message.blocked_reason,
                       "artifacts": list(message.artifacts),
                       "test_evidence": list(message.test_evidence)}
            payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            if self._event_exists(db, message.event_id, payload_json):
                db.commit()
                return False
            task_row = db.execute("SELECT payload_json FROM tasks WHERE task_id=?",
                                  (message.task_id,)).fetchone()
            if task_row is None:
                raise LeaseError(f"unknown task: {message.task_id}")
            task = TaskRecord.from_dict(json.loads(task_row["payload_json"]))
            decision = validate_control(message, task)
            if not decision.accepted:
                raise ControlRejected("CONTROL rejected: " + "; ".join(decision.findings))
            target = message.requested_state or task.state
            if target is TaskState.NEEDS_REVIEW:
                if not self._has_passed_test_run(db, task.task_id, message.head_sha,
                                                 message.test_evidence):
                    raise ControlRejected("needs_review requires a registered passed test run for head_sha")
            if target is not task.state and not allowed_transition(
                task.state, target, assigned_worker=True
            ):
                raise ControlRejected(f"illegal CONTROL transition: {task.state.value} -> {target.value}")
            updated = replace(task, state=target,
                              test_evidence=message.test_evidence or task.test_evidence)
            projection = None
            if updated != task:
                projection = (_prepare_plan_projection(
                    project_root, task.task_id, updated.state, task.assigned_worker,
                    expected_sha256=expected_plan_sha256,
                ) if project_root is not None else None)
                if projection is not None:
                    pending = db.execute(
                        "SELECT event_id FROM plan_projection_outbox WHERE project_root=? LIMIT 1",
                        (projection["project_root"],),
                    ).fetchone()
                    if pending is not None:
                        raise LeaseError(
                            f"PLAN projection {pending['event_id']} must be recovered before another write"
                        )
                db.execute("UPDATE tasks SET payload_json=?, updated_at=? WHERE task_id=?",
                           (json.dumps(updated.to_dict(), sort_keys=True, separators=(",", ":")),
                            current, task.task_id))
            self._insert_event(db, message.event_id, lease.generation, payload_json, current)
            if projection is not None and (
                projection["expected_sha256"] != projection["projected_sha256"]
            ):
                db.execute(
                    "INSERT INTO plan_projection_outbox VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (message.event_id, projection["project_root"],
                     projection["expected_sha256"], projection["projected_sha256"],
                     projection["projected_text"], current, lease.generation),
                )
            db.commit()
            try:
                self.apply_pending_plan_projections(lease, now=now)
            except Exception as exc:
                raise PlanProjectionPending(message.event_id, str(exc)) from exc
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _has_passed_test_run(db: sqlite3.Connection, task_id: str, sha: str | None,
                             evidence_refs: tuple[str, ...]) -> bool:
        if not sha or not evidence_refs:
            return False
        rows = db.execute(
            "SELECT * FROM test_run_receipts WHERE task_id=? AND sha=? AND evidence_ref IN ("
            + ",".join("?" for _ in evidence_refs) + ")",
            (task_id, sha, *evidence_refs),
        ).fetchall()
        for row in rows:
            try:
                artifact_bytes = Path(row["artifact_path"]).read_bytes()
                artifact = json.loads(artifact_bytes.decode("utf-8"))
                if (hashlib.sha256(artifact_bytes).hexdigest() != row["artifact_sha256"]
                        or not artifact.get("passed") or artifact.get("sha") != sha
                        or artifact.get("output_sha256") != row["output_sha256"]
                        or hashlib.sha256(Path(artifact["log_path"]).read_bytes()).hexdigest()
                        != row["output_sha256"]):
                    continue
                return True
            except (OSError, UnicodeError, ValueError, KeyError, TypeError):
                continue
        return False

    def transition_task(self, lease: HeadLease, task_id: str, target: TaskState, *,
                        event_id: str, expected_state: TaskState,
                        head_sha: str | None = None,
                        now: float | None = None) -> bool:
        """Apply a lease-authorized state transition and its event atomically."""
        self.apply_pending_plan_projections(lease, now=now)
        return self._transition_task(
            lease, task_id, target, event_id=event_id, expected_state=expected_state,
            head_sha=head_sha, now=now, projection=None,
        )

    def transition_task_with_plan(self, lease: HeadLease, task_id: str,
                                  target: TaskState, *, event_id: str,
                                  expected_state: TaskState,
                                  project_root: str | Path,
                                  expected_plan_sha256: str,
                                  head_sha: str | None = None,
                                  now: float | None = None) -> bool:
        """Transition task state and durably queue its PLAN projection together.

        The SQLite event, task update, and projection intent commit atomically.
        The PLAN file is then replaced atomically while holding the same SQLite
        writer lock used by HEAD lease changes. If replacement fails, the
        committed projection intent remains for a later leased recovery call.
        """
        current = time.time() if now is None else now
        task = self.get_task(task_id)
        if task is None:
            raise LeaseError(f"unknown task: {task_id}")
        if not isinstance(expected_plan_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", expected_plan_sha256
        ):
            raise ValueError("expected_plan_sha256 must be a lowercase SHA-256 digest")
        root, plan_path = _resolve_plan_path(project_root)
        source_bytes = plan_path.read_bytes()
        actual_sha = hashlib.sha256(source_bytes).hexdigest()
        if actual_sha != expected_plan_sha256:
            db = self._connect()
            try:
                db.execute("BEGIN IMMEDIATE")
                self._assert_current(db, lease, current)
                existing = db.execute(
                    "SELECT payload_json FROM state_events WHERE event_id=?", (event_id,)
                ).fetchone()
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
            if existing is not None:
                try:
                    payload = json.loads(existing["payload_json"])
                except (TypeError, ValueError):
                    payload = None
                if (isinstance(payload, dict)
                        and payload.get("type") == "task.transitioned"
                        and payload.get("task_id") == task_id
                        and payload.get("from") == expected_state.value
                        and payload.get("to") == target.value
                        and payload.get("head_sha") == head_sha
                        and payload.get("projected_plan_sha256") == actual_sha):
                    self.apply_pending_plan_projections(lease, now=now)
                    return False
            raise PlanWriteConflict("PLAN changed since it was read")
        source = source_bytes.decode("utf-8")
        projected_text, projected_sha = patch_plan_task_state(
            source, task_id=task_id, state=target,
            assigned_worker=task.assigned_worker,
            expected_sha256=expected_plan_sha256,
        )
        projection = {
            "project_root": str(root),
            "expected_sha256": actual_sha,
            "projected_sha256": projected_sha,
            "projected_text": projected_text.encode("utf-8"),
        }
        applied = self._transition_task(
            lease, task_id, target, event_id=event_id, expected_state=expected_state,
            head_sha=head_sha, now=now, projection=projection,
        )
        try:
            self.apply_pending_plan_projections(lease, now=now)
        except Exception as exc:
            raise PlanProjectionPending(event_id, str(exc)) from exc
        return applied

    def apply_pending_plan_projections(self, lease: HeadLease, *,
                                      now: float | None = None) -> tuple[str, ...]:
        """Replay pending PLAN writes idempotently under a current HEAD lease."""
        current = time.time() if now is None else now
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            rows = db.execute(
                "SELECT * FROM plan_projection_outbox ORDER BY created_at, event_id"
            ).fetchall()
            applied: list[str] = []
            for row in rows:
                root, plan_path = _resolve_plan_path(row["project_root"])
                expected = row["expected_sha256"]
                projected = row["projected_sha256"]
                content = bytes(row["projected_text"])
                if hashlib.sha256(content).hexdigest() != projected:
                    raise PlanWriteConflict("queued PLAN projection content hash is invalid")
                current_bytes = plan_path.read_bytes()
                current_sha = hashlib.sha256(current_bytes).hexdigest()
                if current_sha == projected:
                    pass  # crash after atomic replace but before outbox removal
                elif current_sha == expected:
                    _atomic_replace_plan(root, plan_path, content, expected)
                else:
                    raise PlanWriteConflict(
                        "PLAN no longer matches queued projection precondition or result"
                    )
                db.execute("DELETE FROM plan_projection_outbox WHERE event_id=?",
                           (row["event_id"],))
                applied.append(row["event_id"])
            # If the lease expires during filesystem work, leave the outbox rows
            # intact; the next HEAD can confirm the already-applied hashes.
            self._assert_current(db, lease, time.time() if now is None else current)
            db.commit()
            return tuple(applied)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def pending_plan_projections(self) -> tuple[dict, ...]:
        """Return non-content metadata for projection recovery diagnostics."""
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT event_id, project_root, expected_sha256, projected_sha256, "
                "created_at, generation FROM plan_projection_outbox "
                "ORDER BY created_at, event_id"
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def _transition_task(self, lease: HeadLease, task_id: str, target: TaskState, *,
                         event_id: str, expected_state: TaskState,
                         head_sha: str | None, now: float | None,
                         projection: dict | None) -> bool:
        current = time.time() if now is None else now
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            payload = {"type": "task.transitioned", "task_id": task_id,
                       "from": expected_state.value, "to": target.value,
                       "head_sha": head_sha}
            if projection is not None:
                payload["projected_plan_sha256"] = projection["projected_sha256"]
            payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            if self._event_exists(db, event_id, payload_json):
                db.commit()
                return False
            task_row = db.execute("SELECT payload_json FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if task_row is None:
                raise LeaseError(f"unknown task: {task_id}")
            task = TaskRecord.from_dict(json.loads(task_row["payload_json"]))
            if task.state is not expected_state:
                raise LeaseError(f"stale task state: expected {expected_state.value}, found {task.state.value}")
            if target is TaskState.DONE:
                raise LeaseError("done requires an approved typed review and maker-checker validation")
            if not allowed_transition(task.state, target, head_authority=True):
                raise LeaseError(f"illegal task transition: {task.state.value} -> {target.value}")
            if target is TaskState.NEEDS_REVIEW and not task.test_evidence:
                raise LeaseError("needs_review requires test evidence")
            if target is TaskState.NEEDS_REVIEW and not self._has_passed_test_run(
                db, task_id, head_sha, task.test_evidence
            ):
                raise LeaseError("needs_review requires a registered passed test run for head_sha")
            updated = replace(task, state=target)
            if projection is not None:
                pending = db.execute(
                    "SELECT event_id FROM plan_projection_outbox WHERE project_root=? LIMIT 1",
                    (projection["project_root"],),
                ).fetchone()
                if pending is not None:
                    raise LeaseError(
                        f"PLAN projection {pending['event_id']} must be recovered before another write"
                    )
            db.execute("UPDATE tasks SET payload_json=?, updated_at=? WHERE task_id=?",
                       (json.dumps(updated.to_dict(), sort_keys=True, separators=(",", ":")),
                        current, task_id))
            self._insert_event(db, event_id, lease.generation, payload_json, current)
            if projection is not None:
                db.execute(
                    "INSERT INTO plan_projection_outbox VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (event_id, projection["project_root"], projection["expected_sha256"],
                     projection["projected_sha256"], projection["projected_text"],
                     current, lease.generation),
                )
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def start_task_invocation(self, lease: HeadLease, task_id: str, invocation_id: str, *,
                              project_root: str | Path | None = None,
                              expected_plan_sha256: str | None = None,
                              cancellation_token: str | None = None,
                              now: float | None = None) -> bool:
        """Atomically start a maker and optionally project in-progress state to PLAN."""
        if not isinstance(invocation_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", invocation_id
        ):
            raise ValueError("invocation_id is invalid")
        if cancellation_token is not None and (
                not isinstance(cancellation_token, str) or len(cancellation_token) < 32):
            raise ValueError("cancellation_token must be at least 32 characters or null")
        cancellation_token_sha256 = (
            hashlib.sha256(cancellation_token.encode("utf-8")).hexdigest()
            if cancellation_token is not None else None
        )
        cancellation_hmac_key = cancellation_token
        current = time.time() if now is None else now
        if expected_plan_sha256 is not None and (
                not isinstance(expected_plan_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_plan_sha256)):
            raise ValueError("expected_plan_sha256 must be a lowercase SHA-256 digest")
        self.apply_pending_plan_projections(lease, now=now)
        projection = None
        projected_task = self.get_task(task_id)
        if project_root is not None:
            if projected_task is None:
                raise LeaseError(f"unknown task: {task_id}")
            projection = _prepare_plan_projection(
                project_root, task_id, TaskState.IN_PROGRESS,
                projected_task.assigned_worker,
                expected_sha256=expected_plan_sha256,
            )
        event_id = f"invocation-start:{invocation_id}"
        payload = {"type": "task.invocation_started", "task_id": task_id,
                   "invocation_id": invocation_id}
        if cancellation_token_sha256 is not None:
            payload["termination_token_sha256"] = cancellation_token_sha256
        if projection is not None:
            payload["projected_plan_sha256"] = projection["projected_sha256"]
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            mode = db.execute("SELECT mode FROM supervisor_mode WHERE singleton=1").fetchone()
            if mode is None or mode["mode"] != "running":
                raise LeaseError("task invocation requires supervisor running mode")
            if self._event_exists(db, event_id, payload_json):
                db.commit()
                return False
            row = db.execute("SELECT payload_json FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise LeaseError(f"unknown task: {task_id}")
            task = TaskRecord.from_dict(json.loads(row["payload_json"]))
            if (task.state is not TaskState.CLAIMED or not task.assigned_worker
                    or task.maker_identity is None):
                raise LeaseError("maker launch requires a claimed task with a maker identity")
            if projection is not None:
                if (projected_task is None
                        or projected_task.assigned_worker != task.assigned_worker):
                    raise LeaseError("task assignment changed while preparing PLAN projection")
                pending = db.execute(
                    "SELECT event_id FROM plan_projection_outbox WHERE project_root=? LIMIT 1",
                    (projection["project_root"],),
                ).fetchone()
                if pending is not None:
                    raise LeaseError(
                        f"PLAN projection {pending['event_id']} must be recovered before another write"
                    )
            updated = replace(task, state=TaskState.IN_PROGRESS)
            db.execute("UPDATE tasks SET payload_json=?, updated_at=? WHERE task_id=?",
                       (json.dumps(updated.to_dict(), sort_keys=True, separators=(",", ":")),
                        current, task_id))
            db.execute(
                "INSERT INTO task_invocation_liveness "
                "(task_id, invocation_id, state, started_at, heartbeat_at, generation, "
                "termination_hmac_key) VALUES(?, ?, 'running', ?, ?, ?, ?)",
                (task_id, invocation_id, current, current, lease.generation,
                 cancellation_hmac_key),
            )
            self._insert_event(db, event_id, lease.generation, payload_json, current)
            if (projection is not None
                    and projection["expected_sha256"] != projection["projected_sha256"]):
                db.execute(
                    "INSERT INTO plan_projection_outbox VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (event_id, projection["project_root"], projection["expected_sha256"],
                     projection["projected_sha256"], projection["projected_text"],
                     current, lease.generation),
                )
            db.commit()
            if projection is not None:
                try:
                    self.apply_pending_plan_projections(
                        lease, now=now)
                except Exception as exc:
                    raise PlanProjectionPending(event_id, str(exc)) from exc
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def start_checker_invocation(self, lease: HeadLease, task_id: str,
                                 checker_identity: dict[str, str], *,
                                 sha: str, gate_fingerprint: str,
                                 invocation_id: str,
                                 now: float | None = None) -> bool:
        """Atomically reserve one checker launch after gate and separation checks."""
        if (not isinstance(checker_identity, dict)
                or set(checker_identity) != {"unit_id", "runtime", "model"}
                or not all(isinstance(value, str) and value.strip()
                           for value in checker_identity.values())):
            raise ValueError("checker identity must snapshot unit_id, runtime, and model")
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", sha or ""):
            raise ValueError("checker SHA must be a full Git SHA")
        if not isinstance(gate_fingerprint, str) or not gate_fingerprint.strip():
            raise ValueError("checker gate fingerprint is required")
        if not isinstance(invocation_id, str) or not invocation_id.strip():
            raise ValueError("checker invocation ID is required")
        current = time.time() if now is None else now
        payload = {"type": "checker.started", "task_id": task_id,
                   "checker": dict(checker_identity), "sha": sha,
                   "gate_fingerprint": gate_fingerprint, "invocation_id": invocation_id}
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            mode = db.execute("SELECT mode FROM supervisor_mode WHERE singleton=1").fetchone()
            if mode is None or mode["mode"] != "running":
                raise LeaseError("checker invocation requires supervisor running mode")
            if self._event_exists(db, "checker-start:" + invocation_id, payload_json):
                db.commit()
                return False
            task_row = db.execute("SELECT payload_json FROM tasks WHERE task_id=?",
                                  (task_id,)).fetchone()
            if task_row is None:
                raise LeaseError(f"unknown checker task: {task_id}")
            task = TaskRecord.from_dict(json.loads(task_row["payload_json"]))
            maker = task.maker_identity
            if task.state is not TaskState.NEEDS_REVIEW or maker is None:
                raise LeaseError("checker launch requires a task awaiting review with maker identity")
            if (checker_identity.get("unit_id") == maker["unit_id"]
                    or (checker_identity.get("runtime") == maker["runtime"]
                        and checker_identity.get("model") == maker["model"])):
                raise LeaseError("checker identity is not independent of maker")
            receipt = db.execute(
                "SELECT artifact_path, artifact_sha256 FROM gate_receipts "
                "WHERE task_id=? AND sha=? AND fingerprint=?",
                (task_id, sha, gate_fingerprint),
            ).fetchone()
            if receipt is None or self._file_sha256(receipt["artifact_path"]) != receipt["artifact_sha256"]:
                raise LeaseError("checker launch requires an unchanged HEAD-registered gate")
            self._insert_event(db, "checker-start:" + invocation_id, lease.generation,
                               payload_json, current)
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def apply_review(self, lease: HeadLease, verdict: ReviewVerdict, gate: GateResult, *,
                     checker_invocation_id: str, event_id: str,
                     project_root: str | Path | None = None,
                     expected_plan_sha256: str | None = None,
                     now: float | None = None) -> bool:
        """Atomically persist an approved review and task completion under HEAD lease."""
        current = time.time() if now is None else now
        if project_root is None and expected_plan_sha256 is not None:
            raise ValueError("expected_plan_sha256 requires project_root")
        self.apply_pending_plan_projections(lease, now=now)
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            receipt = db.execute("SELECT * FROM gate_receipts WHERE task_id=? AND sha=? AND fingerprint=?",
                                 (verdict.task_id, verdict.sha, verdict.gate_fingerprint)).fetchone()
            if receipt is None:
                raise LeaseError("review requires a gate receipt registered by HEAD")
            if (receipt["artifact_path"] != gate.artifact_path
                    or receipt["artifact_sha256"] != self._file_sha256(receipt["artifact_path"])):
                raise LeaseError("registered gate artifact is missing or has changed")
            checker_run = db.execute("SELECT * FROM invocation_receipts WHERE invocation_id=?",
                                     (checker_invocation_id,)).fetchone()
            if (checker_run is None or checker_run["task_id"] != verdict.task_id
                    or checker_run["purpose"] != "checker" or checker_run["status"] != "succeeded"
                    or checker_run["exit_code"] != 0
                    or checker_run["unit_id"] != verdict.checker.unit_id
                    or checker_run["runtime"] != verdict.checker.runtime
                    or checker_run["model"] != verdict.checker.model
                    or checker_run["review_sha"] != verdict.sha
                    or checker_run["gate_fingerprint"] != verdict.gate_fingerprint):
                raise LeaseError("review requires a matching successful checker invocation receipt")
            task_row = db.execute("SELECT payload_json FROM tasks WHERE task_id=?",
                                  (verdict.task_id,)).fetchone()
            if task_row is None:
                raise LeaseError(f"unknown task: {verdict.task_id}")
            task = TaskRecord.from_dict(json.loads(task_row["payload_json"]))
            maker_run = db.execute(
                "SELECT invocation_id FROM invocation_receipts WHERE task_id=? AND purpose='maker' "
                "AND status='succeeded' AND exit_code=0 AND finished_at<=? "
                "ORDER BY finished_at DESC, invocation_id DESC LIMIT 1",
                (verdict.task_id, checker_run["started_at"]),
            ).fetchone()
            maker_invocation_id = maker_run["invocation_id"] if maker_run else None
            memory_injection_event_id = None
            if maker_invocation_id:
                maker_event = db.execute(
                    "SELECT payload_json FROM state_events WHERE event_id=?",
                    ("invocation:" + maker_invocation_id,),
                ).fetchone()
                if maker_event:
                    maker_payload = json.loads(maker_event["payload_json"])
                    memory_injection_event_id = maker_payload.get("memory_injection_event_id")
            if verdict.decision == "approved":
                target = TaskState.DONE
            elif verdict.decision == "changes_requested":
                target = TaskState.IN_PROGRESS
            else:
                target = TaskState.BLOCKED
            payload = {"type": "task.reviewed", "task_id": verdict.task_id,
                       "decision": verdict.decision, "to": target.value,
                       "sha": verdict.sha, "gate_fingerprint": verdict.gate_fingerprint,
                       "maker_identity": task.maker_identity,
                       "checker": {"unit_id": verdict.checker.unit_id,
                                   "runtime": verdict.checker.runtime,
                                   "model": verdict.checker.model},
                       "rationale": verdict.rationale,
                       "evidence_refs": list(verdict.evidence_refs),
                       "gate_artifact_sha256": receipt["artifact_sha256"],
                       "maker_invocation_id": maker_invocation_id,
                       "memory_injection_event_id": memory_injection_event_id,
                       "checker_invocation_id": checker_invocation_id,
                       "review_digest": review_digest(verdict)}
            payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            if self._event_exists(db, event_id, payload_json):
                db.commit()
                return False
            if task.owns_failures:
                artifact = json.loads(Path(receipt["artifact_path"]).read_text(encoding="utf-8"))
                currently_open = []
                for owner_id in task.owns_failures:
                    owner_row = db.execute("SELECT payload_json FROM tasks WHERE task_id=?",
                                           (owner_id,)).fetchone()
                    if owner_row is not None and TaskRecord.from_dict(
                        json.loads(owner_row["payload_json"])
                    ).state is not TaskState.DONE:
                        currently_open.append(owner_id)
                if artifact.get("open_failure_owner_ids") != sorted(currently_open):
                    raise LeaseError("inherited failure ownership changed after gate registration")
            findings = validate_review(task, gate, verdict)
            if findings:
                raise LeaseError("review rejected: " + "; ".join(findings))
            if not allowed_transition(task.state, target, head_authority=True):
                raise LeaseError(f"illegal review transition: {task.state.value} -> {target.value}")
            projection = (_prepare_plan_projection(
                project_root, task.task_id, target, task.assigned_worker,
                expected_sha256=expected_plan_sha256,
            ) if project_root is not None else None)
            if projection is not None:
                pending = db.execute(
                    "SELECT event_id FROM plan_projection_outbox WHERE project_root=? LIMIT 1",
                    (projection["project_root"],),
                ).fetchone()
                if pending is not None:
                    raise LeaseError(
                        f"PLAN projection {pending['event_id']} must be recovered before another write"
                    )
            updated = replace(task, state=target)
            db.execute("UPDATE tasks SET payload_json=?, updated_at=? WHERE task_id=?",
                       (json.dumps(updated.to_dict(), sort_keys=True, separators=(",", ":")),
                        current, verdict.task_id))
            self._insert_event(db, event_id, lease.generation, payload_json, current)
            if projection is not None and (
                projection["expected_sha256"] != projection["projected_sha256"]
            ):
                db.execute(
                    "INSERT INTO plan_projection_outbox VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (event_id, projection["project_root"],
                     projection["expected_sha256"], projection["projected_sha256"],
                     projection["projected_text"], current, lease.generation),
                )
            db.commit()
            try:
                self.apply_pending_plan_projections(lease, now=now)
            except Exception as exc:
                raise PlanProjectionPending(event_id, str(exc)) from exc
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def record_invocation(self, lease: HeadLease, result: InvocationResult, *,
                          now: float | None = None) -> bool:
        """Append a runtime receipt under HEAD authority without storing prompts or output."""
        current = time.time() if now is None else now
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            if result.task_id:
                task_row = db.execute("SELECT payload_json FROM tasks WHERE task_id=?",
                                      (result.task_id,)).fetchone()
                if task_row is None:
                    raise LeaseError(f"unknown invocation task: {result.task_id}")
                task = TaskRecord.from_dict(json.loads(task_row["payload_json"]))
                if result.purpose == "maker" and (
                    task.assigned_worker != result.unit_id or task.maker_identity is None
                    or task.maker_identity != {"unit_id": result.unit_id,
                                               "runtime": result.runtime, "model": result.model}
                ):
                    raise LeaseError("maker invocation identity does not match assigned maker snapshot")
                if result.purpose == "checker":
                    maker = task.maker_identity
                    if (task.state is not TaskState.NEEDS_REVIEW or not result.review_sha
                            or not result.gate_fingerprint):
                        raise LeaseError("checker run requires a review-state task and exact gate binding")
                    gate_row = db.execute("SELECT 1 FROM gate_receipts "
                                          "WHERE task_id=? AND sha=? AND fingerprint=?",
                                          (result.task_id, result.review_sha,
                                           result.gate_fingerprint)).fetchone()
                    if gate_row is None:
                        raise LeaseError("checker run requires the matching HEAD-registered gate receipt")
                    if maker is None or maker["unit_id"] == result.unit_id:
                        raise LeaseError("checker invocation must differ from the task maker")
                    if maker["runtime"] == result.runtime and maker["model"] == result.model:
                        raise LeaseError("checker invocation must use an independent runtime/model")
                    reservation_row = db.execute(
                        "SELECT payload_json, created_at FROM state_events WHERE event_id=?",
                        ("checker-start:" + result.invocation_id,),
                    ).fetchone()
                    expected_reservation = {
                        "type": "checker.started",
                        "task_id": result.task_id,
                        "checker": {"unit_id": result.unit_id,
                                    "runtime": result.runtime,
                                    "model": result.model},
                        "sha": result.review_sha,
                        "gate_fingerprint": result.gate_fingerprint,
                        "invocation_id": result.invocation_id,
                    }
                    if (reservation_row is None
                            or json.loads(reservation_row["payload_json"]) != expected_reservation
                            or result.started_at < reservation_row["created_at"]):
                        raise LeaseError(
                            "checker invocation does not match its HEAD-reserved identity, gate, and start time"
                        )
            payload = {"type": "runtime.invoked", "invocation_id": result.invocation_id,
                       "task_id": result.task_id, "purpose": result.purpose,
                       "unit_id": result.unit_id, "runtime": result.runtime,
                       "role": result.role or "unknown", "model": result.model,
                       "status": result.status,
                       "exit_code": result.exit_code, "output_sha256": result.output_sha256,
                       "review_sha": result.review_sha,
                       "gate_fingerprint": result.gate_fingerprint,
                       "started_at": result.started_at, "finished_at": result.finished_at,
                       "duration_seconds": result.duration_seconds,
                       "input_tokens": result.input_tokens,
                       "output_tokens": result.output_tokens,
                       "cached_input_tokens": result.cached_input_tokens,
                       "reported_cost_usd": result.reported_cost_usd,
                       "memory_injection_event_id": result.memory_injection_event_id}
            payload["process_tree_cancel_method"] = result.process_tree_cancel_method
            payload["process_tree_cancel_verified"] = result.process_tree_cancel_verified
            payload["process_tree_cancel_exit_code"] = result.process_tree_cancel_exit_code
            payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            existing = db.execute("SELECT * FROM invocation_receipts WHERE invocation_id=?",
                                  (result.invocation_id,)).fetchone()
            if existing:
                if any(existing[key] != value for key, value in {
                    "task_id": result.task_id, "purpose": result.purpose,
                    "unit_id": result.unit_id, "runtime": result.runtime,
                    "model": result.model, "status": result.status,
                    "exit_code": result.exit_code, "output_sha256": result.output_sha256,
                    "review_sha": result.review_sha,
                    "gate_fingerprint": result.gate_fingerprint,
                    "process_tree_cancel_method": result.process_tree_cancel_method,
                    "process_tree_cancel_verified": (None if result.process_tree_cancel_verified is None
                                                     else int(result.process_tree_cancel_verified)),
                    "process_tree_cancel_exit_code": result.process_tree_cancel_exit_code,
                }.items()):
                    raise LeaseError("invocation_id already has a different receipt")
                prior_event = db.execute("SELECT payload_json FROM state_events WHERE event_id=?",
                                         ("invocation:" + result.invocation_id,)).fetchone()
                if (prior_event is None or json.loads(prior_event["payload_json"]) != payload):
                    raise LeaseError("invocation_id already has different usage metadata")
                db.commit()
                return False
            db.execute("INSERT INTO invocation_receipts "
                       "(invocation_id, task_id, purpose, unit_id, runtime, model, status, "
                       "exit_code, output_sha256, started_at, finished_at, generation, "
                       "review_sha, gate_fingerprint, process_tree_cancel_method, "
                       "process_tree_cancel_verified, process_tree_cancel_exit_code) "
                       "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (result.invocation_id, result.task_id, result.purpose, result.unit_id,
                        result.runtime, result.model, result.status, result.exit_code,
                        result.output_sha256, result.started_at, result.finished_at,
                        lease.generation, result.review_sha, result.gate_fingerprint,
                        result.process_tree_cancel_method,
                        (None if result.process_tree_cancel_verified is None
                         else int(result.process_tree_cancel_verified)),
                        result.process_tree_cancel_exit_code))
            if (result.purpose == "maker" and result.task_id
                    and result.process_tree_cancel_verified is not False):
                db.execute(
                    "UPDATE task_invocation_liveness SET state='completed', heartbeat_at=?, "
                    "generation=? WHERE task_id=? AND invocation_id=? AND state='running'",
                    (current, lease.generation, result.task_id, result.invocation_id),
                )
            self._insert_event(db, "invocation:" + result.invocation_id, lease.generation,
                               payload_json, current)
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def record_gate_attempt(self, lease: HeadLease, gate: GateResult, *,
                            event_id: str, now: float | None = None) -> bool:
        """Record a completed passed/failed mechanical gate without output text."""
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("event_id must be non-empty")
        if gate.status not in {"passed", "failed"} or not gate.artifact_path:
            raise LeaseError("gate attempt requires a completed gate artifact")
        raw_path = Path(gate.artifact_path)
        if raw_path.is_symlink() or not raw_path.is_file():
            raise LeaseError("gate attempt artifact must be a regular file")
        artifact_path = raw_path.resolve(strict=True)
        try:
            artifact_bytes = artifact_path.read_bytes()
            artifact = json.loads(artifact_bytes.decode("utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise LeaseError("gate attempt artifact cannot be read") from exc
        if (artifact.get("task_id") != gate.task_id or artifact.get("sha") != gate.sha
                or artifact.get("fingerprint") != gate.fingerprint
                or artifact.get("status") != gate.status):
            raise LeaseError("gate attempt artifact does not match the supplied result")
        checks = artifact.get("checks")
        if not isinstance(checks, dict):
            raise LeaseError("gate attempt artifact has malformed check results")
        statuses = {name: item.get("status") for name, item in checks.items()
                    if isinstance(name, str) and isinstance(item, dict)}
        supplied_statuses = {name: item.status for name, item in gate.checks.items()}
        if (len(statuses) != len(checks) or not statuses
                or any(status not in {"passed", "failed", "skipped"}
                       for status in statuses.values())
                or supplied_statuses != statuses
                or list(gate.new_failures) != artifact.get("new_failures", [])
                or list(gate.inherited_failures) != artifact.get("inherited_failures", [])):
            raise LeaseError("gate attempt artifact has malformed check results")
        for field_name in ("new_failures", "inherited_failures"):
            values = artifact.get(field_name, [])
            if (not isinstance(values, list)
                    or not all(isinstance(value, str) and value in statuses for value in values)):
                raise LeaseError("gate attempt artifact has malformed failure names")
        digest = hashlib.sha256(artifact_bytes).hexdigest()
        attempt_id = "gate-attempt:" + event_id
        payload = {"type": "gate.attempted", "task_id": gate.task_id, "sha": gate.sha,
                   "fingerprint": gate.fingerprint, "status": gate.status,
                   "artifact_sha256": digest, "checks": statuses,
                   "new_failures": artifact.get("new_failures", []),
                   "inherited_failures": artifact.get("inherited_failures", [])}
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        current = time.time() if now is None else now
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            if db.execute("SELECT 1 FROM tasks WHERE task_id=?", (gate.task_id,)).fetchone() is None:
                raise LeaseError(f"unknown task: {gate.task_id}")
            existing = db.execute("SELECT * FROM gate_attempt_receipts WHERE attempt_event_id=?",
                                  (attempt_id,)).fetchone()
            if existing:
                event = db.execute("SELECT payload_json FROM state_events WHERE event_id=?",
                                   (attempt_id,)).fetchone()
                if (existing["artifact_sha256"] != digest or event is None
                        or event["payload_json"] != payload_json):
                    raise LeaseError("gate attempt event ID was reused for different evidence")
                db.commit()
                return False
            db.execute("INSERT INTO gate_attempt_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (attempt_id, gate.task_id, gate.sha, gate.fingerprint, str(artifact_path),
                        digest, gate.status, current, lease.generation))
            self._insert_event(db, attempt_id, lease.generation, payload_json, current)
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def record_gate_result(self, lease: HeadLease, gate: GateResult, *,
                           now: float | None = None) -> str:
        """Register an immutable passed gate artifact under active HEAD authority."""
        if gate.status != "passed" or not gate.artifact_path:
            raise LeaseError("only passed gate results with an artifact can be registered")
        artifact_path = Path(gate.artifact_path).resolve()
        try:
            artifact_bytes = artifact_path.read_bytes()
            artifact = json.loads(artifact_bytes.decode("utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise LeaseError(f"gate artifact cannot be read: {exc}") from exc
        if (artifact.get("task_id") != gate.task_id or artifact.get("sha") != gate.sha
                or artifact.get("fingerprint") != gate.fingerprint
                or artifact.get("status") != "passed"):
            raise LeaseError("gate artifact does not match the supplied result")
        if gate_artifact_findings(artifact):
            raise LeaseError("gate artifact is missing valid passed required evidence")
        digest = hashlib.sha256(artifact_bytes).hexdigest()
        current = time.time() if now is None else now
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._assert_current(db, lease, current)
            task = db.execute("SELECT payload_json FROM tasks WHERE task_id=?",
                              (gate.task_id,)).fetchone()
            if task is None:
                raise LeaseError(f"unknown task: {gate.task_id}")
            task_record = TaskRecord.from_dict(json.loads(task["payload_json"]))
            if artifact.get("failure_owner_ids") != list(task_record.owns_failures):
                raise LeaseError("gate failure ownership does not match the registered task")
            active_owners = []
            for owner_id in task_record.owns_failures:
                owner_row = db.execute("SELECT payload_json FROM tasks WHERE task_id=?",
                                       (owner_id,)).fetchone()
                if owner_row is not None and TaskRecord.from_dict(
                    json.loads(owner_row["payload_json"])
                ).state is not TaskState.DONE:
                    active_owners.append(owner_id)
            if artifact.get("open_failure_owner_ids") != sorted(active_owners):
                raise LeaseError("gate open failure owners do not match current task state")
            existing = db.execute("SELECT artifact_sha256 FROM gate_receipts "
                                  "WHERE task_id=? AND sha=? AND fingerprint=?",
                                  (gate.task_id, gate.sha, gate.fingerprint)).fetchone()
            if existing and existing["artifact_sha256"] != digest:
                raise LeaseError("a different artifact is already registered for this gate fingerprint")
            db.execute("INSERT OR IGNORE INTO gate_receipts VALUES(?, ?, ?, ?, ?, ?, ?)",
                       (gate.task_id, gate.sha, gate.fingerprint, str(artifact_path), digest,
                        current, lease.generation))
            db.commit()
            return digest
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def has_registered_gate(self, lease: HeadLease, task_id: str, sha: str,
                            fingerprint: str, *, now: float | None = None) -> bool:
        """Check a live HEAD lease and an unchanged registered gate artifact."""
        current = time.time() if now is None else now
        with closing(self._connect()) as db:
            self._assert_current(db, lease, current)
            row = db.execute(
                "SELECT artifact_path, artifact_sha256 FROM gate_receipts "
                "WHERE task_id=? AND sha=? AND fingerprint=?",
                (task_id, sha, fingerprint),
            ).fetchone()
        return bool(row and self._file_sha256(row["artifact_path"]) == row["artifact_sha256"])

    @staticmethod
    def _file_sha256(path: str | Path) -> str:
        try:
            return hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except OSError:
            return ""

    def get_task(self, task_id: str) -> TaskRecord | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT payload_json FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return TaskRecord.from_dict(json.loads(row["payload_json"])) if row else None

    def list_tasks(self) -> tuple[TaskRecord, ...]:
        """Return the current task projection in stable identifier order."""
        with closing(self._connect()) as db:
            rows = db.execute("SELECT payload_json FROM tasks ORDER BY task_id").fetchall()
        return tuple(TaskRecord.from_dict(json.loads(row["payload_json"])) for row in rows)

    @staticmethod
    def _event_exists(db: sqlite3.Connection, event_id: str, payload_json: str) -> bool:
        row = db.execute("SELECT payload_json FROM state_events WHERE event_id=?", (event_id,)).fetchone()
        if row is None:
            return False
        if row["payload_json"] != payload_json:
            raise LeaseError("event_id was already used for a different event payload")
        return True

    @classmethod
    def _insert_event(cls, db: sqlite3.Connection, event_id: str, generation: int,
                      payload_json: str, created_at: float) -> bool:
        if cls._event_exists(db, event_id, payload_json):
            return False
        db.execute("INSERT INTO state_events VALUES(?, ?, ?, ?)",
                   (event_id, generation, payload_json, created_at))
        return True

    @staticmethod
    def _same_lease(row: sqlite3.Row | None, lease: HeadLease) -> bool:
        if not row or row["system_id"] != lease.system_id or row["instance_id"] != lease.instance_id:
            return False
        if row["generation"] != lease.generation:
            return False
        token_hash = hashlib.sha256(lease.token.encode("utf-8")).hexdigest()
        return hmac.compare_digest(row["token_hash"], token_hash)

    @classmethod
    def _assert_current(cls, db: sqlite3.Connection, lease: HeadLease, now: float) -> None:
        row = db.execute("SELECT * FROM head_lease WHERE singleton=1").fetchone()
        if not cls._same_lease(row, lease) or row["expires_at"] <= now:
            raise LeaseError("HEAD lease is absent, expired, or fenced by a newer owner")
