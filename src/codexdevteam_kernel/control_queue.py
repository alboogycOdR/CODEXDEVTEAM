"""Untrusted immutable CONTROL outbox files and HEAD-side draining."""

import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from .control import ControlMessage
from .state import ControlRejected, HeadLease, StateStore
from .tasks import TaskState


_EVENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MAX_MESSAGE_BYTES = 64 * 1024


def submit_control(outbox: str | Path, *, task_id: str, worker_id: str,
                   requested_state: str | None = None, progress_note: str | None = None,
                   blocked_reason: str | None = None, artifacts: tuple[str, ...] = (),
                   test_evidence: tuple[str, ...] = (), head_sha: str | None = None,
                   event_id: str | None = None,
                   invocation_id: str | None = None) -> Path:
    """Atomically write a report. This never opens or mutates authoritative state."""
    event_id = event_id or f"control-{uuid.uuid4().hex}"
    if not _EVENT_ID.fullmatch(event_id):
        raise ValueError("event_id has invalid characters or length")
    for name, values in (("artifacts", artifacts), ("test_evidence", test_evidence)):
        if not isinstance(values, (tuple, list)) or not all(
            isinstance(value, str) and value.strip() for value in values
        ):
            raise ValueError(f"{name} must be an array of non-empty strings")
    message = ControlMessage(
        event_id=event_id, task_id=task_id, worker_id=worker_id,
        requested_state=TaskState(requested_state) if requested_state else None,
        progress_note=progress_note, blocked_reason=blocked_reason,
        artifacts=tuple(artifacts), test_evidence=tuple(test_evidence), head_sha=head_sha,
        invocation_id=invocation_id or os.environ.get("CODEXDEVTEAM_INVOCATION_ID"),
    )
    payload = {
        "protocol_version": message.protocol_version, "event_id": message.event_id,
        "task_id": message.task_id, "worker_id": message.worker_id,
        "requested_state": message.requested_state.value if message.requested_state else None,
        "progress_note": message.progress_note, "blocked_reason": message.blocked_reason,
        "artifacts": list(message.artifacts), "test_evidence": list(message.test_evidence),
        "head_sha": message.head_sha, "invocation_id": message.invocation_id,
    }
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(encoded) > _MAX_MESSAGE_BYTES:
        raise ValueError("CONTROL message exceeds size limit")
    directory = Path(outbox)
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.resolve().is_dir():
        raise ValueError("CONTROL outbox must be a real directory")
    target = directory / f"{event_id}.json"
    fd, temporary = tempfile.mkstemp(prefix=".control-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link creation is exclusive, so duplicate event IDs cannot replace reports.
        os.link(temporary, target)
    except FileExistsError as exc:
        raise ValueError("CONTROL event_id already exists in this outbox") from exc
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return target


def drain_control_outbox(store: StateStore, lease: HeadLease, outbox: str | Path,
                         *, project_root: str | Path | None = None,
                         expected_invocation_id: str | None = None,
                         now: float | None = None) -> dict[str, tuple[str, ...]]:
    """Apply queued reports as HEAD; retain each file in applied/rejected audit dirs."""
    root = Path(outbox)
    if root.is_symlink() or not root.exists():
        return {"applied": (), "rejected": ()}
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("CONTROL outbox must be a directory")
    applied: list[str] = []
    rejected: list[str] = []
    for source in sorted(root.glob("*.json")):
        event_id = source.stem
        try:
            if source.is_symlink() or source.stat().st_size > _MAX_MESSAGE_BYTES:
                raise ValueError("unsafe or oversized CONTROL report")
            data = json.loads(source.read_text(encoding="utf-8"))
            message = ControlMessage.from_dict(data)
            if message.event_id != event_id:
                raise ValueError("CONTROL filename does not match event_id")
            if (expected_invocation_id is not None
                    and message.invocation_id != expected_invocation_id):
                raise ValueError("CONTROL invocation_id does not match the active maker run")
            store.apply_control(lease, message, project_root=project_root, now=now)
            destination_dir = root / "applied"
            applied.append(event_id)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError, ControlRejected):
            destination_dir = root / "rejected"
            rejected.append(event_id)
        if destination_dir.is_symlink():
            raise ValueError(f"CONTROL audit directory is a symlink: {destination_dir.name}")
        destination_dir.mkdir(exist_ok=True)
        if not destination_dir.is_dir() or not destination_dir.resolve().is_relative_to(root):
            raise ValueError(f"CONTROL audit directory escapes outbox: {destination_dir.name}")
        destination = destination_dir / source.name
        if destination.exists():
            destination = destination_dir / f"{event_id}-{uuid.uuid4().hex}.json"
        os.replace(source, destination)
    return {"applied": tuple(applied), "rejected": tuple(rejected)}


def main() -> int:
    """CLI entry point consumes one JSON request from stdin; identity comes from launcher."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Submit an untrusted CODEXDEVTEAM CONTROL report")
    parser.add_argument("--outbox", default=os.environ.get("CODEXDEVTEAM_CONTROL_OUTBOX"))
    parser.add_argument("--task-id", default=os.environ.get("CODEXDEVTEAM_TASK_ID"))
    parser.add_argument("--worker-id", default=os.environ.get("CODEXDEVTEAM_WORKER_ID"))
    args = parser.parse_args()
    if not args.outbox or not args.task_id or not args.worker_id:
        parser.error("outbox, task ID, and worker ID must be supplied by the launcher")
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValueError("CONTROL request must be a JSON object")
        forbidden = set(request) - {"requested_state", "progress_note", "blocked_reason",
                                    "artifacts", "test_evidence", "head_sha"}
        if forbidden:
            raise ValueError("CONTROL request contains forbidden fields")
        for name in ("artifacts", "test_evidence"):
            if name in request and not isinstance(request[name], list):
                raise ValueError(f"{name} must be a JSON array")
        path = submit_control(args.outbox, task_id=args.task_id, worker_id=args.worker_id,
                              **request)
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"CONTROL submission rejected: {exc}", file=sys.stderr)
        return 2
    print(path.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
