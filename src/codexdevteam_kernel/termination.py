"""One-use receipts for process-tree cancellation completed outside a lease."""

import hashlib
import hmac
import json
import os
import re
import tempfile
from pathlib import Path


_INVOCATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def cancellation_receipt_name(invocation_id: str) -> str:
    if not isinstance(invocation_id, str) or not _INVOCATION_ID.fullmatch(invocation_id):
        raise ValueError("invalid invocation_id for cancellation receipt")
    return hashlib.sha256(invocation_id.encode("utf-8")).hexdigest() + ".json"


def write_cancellation_receipt(directory: str | Path, *, token: str,
                               invocation_id: str, task_id: str, method: str,
                               exit_code: int | None, observed_at: float) -> Path:
    """Atomically publish a token-bound termination result from the host adapter."""
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError("cancellation receipt token is invalid")
    name = cancellation_receipt_name(invocation_id)
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("cancellation receipt task_id is invalid")
    if method not in {"windows_taskkill_tree", "posix_process_group"}:
        raise ValueError("unsupported cancellation receipt method")
    if (exit_code is not None
            and (not isinstance(exit_code, int) or isinstance(exit_code, bool))):
        raise ValueError("cancellation receipt exit code must be an integer or null")
    if (not isinstance(observed_at, (int, float)) or isinstance(observed_at, bool)
            or not 0 <= observed_at < float("inf")):
        raise ValueError("cancellation receipt timestamp is invalid")

    root = Path(directory)
    if root.is_symlink():
        raise ValueError("cancellation receipt directory cannot be a symlink")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = root.resolve(strict=True)
    body = {
        "version": 1,
        "invocation_id": invocation_id,
        "task_id": task_id,
        "method": method,
        "verified": True,
        "exit_code": exit_code,
        "observed_at": float(observed_at),
    }
    body_bytes = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    envelope = {
        "payload": body,
        "signature": hmac.new(token.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest(),
    }
    encoded = (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd, raw_temp = tempfile.mkstemp(prefix=".termination-", suffix=".tmp", dir=root)
    temporary = Path(raw_temp)
    target = root / name
    try:
        if target.exists() or target.is_symlink():
            raise FileExistsError("cancellation receipt already exists")
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        fd = -1
        # link() provides create-only publication; never replace another writer's receipt.
        os.link(temporary, target)
        if os.name != "nt":
            directory_fd = os.open(root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return target
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
