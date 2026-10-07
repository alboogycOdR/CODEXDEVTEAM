"""Host-owned capacity observations from configured runtime/provider sources."""

import json
import math
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from .dispatch import CapacityObservation
from .registry import WorkerDefinition, WorkerRegistry


_CAPACITY_FRESHNESS_SECONDS = 120.0
_CAPACITY_QUERY_TIMEOUT_SECONDS = 20.0
_MAX_APP_SERVER_OUTPUT_BYTES = 1_048_576
_MAX_APP_SERVER_QUEUED_LINES = 16


class CapacitySource(Protocol):
    """Provider-neutral interface for converting live capacity into observations."""

    def observe(self, workers: Sequence[WorkerDefinition], *,
                now: float | None = None) -> dict[str, CapacityObservation]:
        """Return fresh observations keyed by configured worker identity."""


def load_host_capacity(config: object, registry: WorkerRegistry, *,
                       command_prefix: Sequence[str]) -> dict[str, CapacityObservation]:
    """Read the configured source and return observations for active workers.

    The manual snapshot source remains an explicit compatibility option. New
    unattended host configurations use the Codex app-server account quota API.
    """
    source = getattr(config, "capacity_source", None)
    if source == "snapshot":
        return config.load_capacity_observations()
    if source != "codex_app_server":
        raise ValueError("unsupported capacity source")
    if not isinstance(registry, WorkerRegistry):
        raise ValueError("Codex capacity source requires a validated worker registry")
    workers = registry.active_workers()
    if not workers:
        raise ValueError("Codex capacity source requires active workers")
    return CodexAppServerCapacitySource(
        command_prefix, cwd=getattr(config, "project_root", None)).observe(workers)


class CodexAppServerCapacitySource:
    """Read Codex ChatGPT-plan capacity using the local app-server JSON-RPC API."""

    def __init__(self, command_prefix: Sequence[str], *, timeout_seconds: float =
                 _CAPACITY_QUERY_TIMEOUT_SECONDS,
                 stale_after_seconds: float = _CAPACITY_FRESHNESS_SECONDS,
                 cwd: str | Path | None = None):
        if (not isinstance(command_prefix, (tuple, list)) or not command_prefix
                or any(not isinstance(part, str) or not part.strip()
                       for part in command_prefix)):
            raise ValueError("capacity source command must be a non-empty argument list")
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or timeout_seconds <= 0 or timeout_seconds > 60):
            raise ValueError("capacity source timeout must be positive and at most 60 seconds")
        if (isinstance(stale_after_seconds, bool)
                or not isinstance(stale_after_seconds, (int, float))
                or stale_after_seconds <= 0 or stale_after_seconds > 3600):
            raise ValueError("capacity source freshness must be positive and at most one hour")
        self.command_prefix = tuple(command_prefix)
        self.timeout_seconds = float(timeout_seconds)
        self.stale_after_seconds = float(stale_after_seconds)
        self.cwd = Path(cwd).resolve() if cwd is not None else None

    def observe(self, workers: Sequence[WorkerDefinition], *,
                now: float | None = None) -> dict[str, CapacityObservation]:
        """Fetch one live account snapshot and conservatively apply its shared bucket."""
        if not workers:
            raise ValueError("capacity observation requires active workers")
        if any(worker.identity.runtime != "codex" for worker in workers):
            raise ValueError("Codex account capacity cannot attest non-Codex workers")
        response = self._read_rate_limits()
        quota_remaining = _shared_codex_quota_remaining(response)
        observed_at = time.time() if now is None else now
        if (isinstance(observed_at, bool) or not isinstance(observed_at, (int, float))
                or not math.isfinite(observed_at) or observed_at <= 0):
            raise ValueError("capacity observation time must be finite and positive")
        available = response["ordinaryUsageAllowed"] and quota_remaining > 0
        observation = CapacityObservation(
            available=available,
            free_slots=1 if available else 0,
            observed_at=float(observed_at),
            stale_after_seconds=self.stale_after_seconds,
            quota_remaining=quota_remaining,
        )
        return {worker.identity.unit_id: observation for worker in workers}

    def _read_rate_limits(self) -> dict[str, object]:
        initialize = {"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "codexdevteam-capacity", "title":
                               "CODEXDEVTEAM capacity source", "version": "0.2.0"},
                "capabilities": {"experimentalApi": True}}}
        initialized = {"method": "initialized"}
        read = {"id": 2, "method": "account/rateLimits/read",
                "params": {"excludeResetCreditDetails": True}}
        environment = {
            name: os.environ[name]
            for name in ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
                         "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                         "CODEX_HOME")
            if name in os.environ
        }
        try:
            process = subprocess.Popen(
                [*self.command_prefix, "app-server", "--listen", "stdio://"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", bufsize=1, env=environment, cwd=self.cwd,
            )
        except OSError as exc:
            raise ValueError("Codex app-server capacity query failed to launch") from exc
        if process.stdin is None or process.stdout is None:
            _stop_app_server(process)
            raise ValueError("Codex app-server stdio transport is unavailable")
        output: queue.Queue[str | None] = queue.Queue(maxsize=_MAX_APP_SERVER_QUEUED_LINES)

        def read_stdout() -> None:
            try:
                for line in process.stdout:
                    output.put(line)
            finally:
                try:
                    output.put(None, timeout=0.1)
                except queue.Full:
                    pass

        reader = threading.Thread(target=read_stdout, daemon=True,
                                  name="codex-capacity-reader")
        reader.start()
        deadline = time.monotonic() + self.timeout_seconds
        methods: set[str] = set()
        init_response = False
        try:
            _write_message(process, initialize)
            init = _read_response(output, deadline, expected_id=1,
                                  methods=methods)
            if "error" in init or not isinstance(init.get("result"), dict):
                raise ValueError("Codex app-server initialize handshake failed")
            init_response = True
            _write_message(process, initialized)
            _write_message(process, read)
            response = _read_response(output, deadline, expected_id=2,
                                      methods=methods)
        except ValueError as exc:
            if str(exc) == "Codex app-server response timed out":
                method_summary = ", ".join(sorted(methods)) or "none"
                raise ValueError(
                    "Codex app-server did not return an account capacity response "
                    f"(methods={method_summary}, "
                    f"init_response={init_response})"
                ) from exc
            raise
        finally:
            _stop_app_server(process)
        if "error" in response or not isinstance(response.get("result"), dict):
            raise ValueError("Codex app-server could not read account capacity")
        return response["result"]


def _write_message(process: subprocess.Popen, message: Mapping[str, object]) -> None:
    if process.stdin is None:
        raise ValueError("Codex app-server stdin closed unexpectedly")
    try:
        process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        process.stdin.flush()
    except (OSError, BrokenPipeError) as exc:
        raise ValueError("Codex app-server closed its request channel") from exc


def _read_response(output: queue.Queue[str | None], deadline: float, *,
                   expected_id: int,
                   methods: set[str]) -> dict[str, object]:
    total_bytes = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Codex app-server response timed out")
        try:
            line = output.get(timeout=remaining)
        except queue.Empty as exc:
            raise ValueError("Codex app-server response timed out") from exc
        if line is None:
            raise ValueError("Codex app-server exited before returning a response")
        total_bytes += len(line.encode("utf-8"))
        if total_bytes > _MAX_APP_SERVER_OUTPUT_BYTES:
            raise ValueError("Codex app-server capacity response exceeded the size limit")
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        method_counter = message.get("method")
        if isinstance(method_counter, str):
            methods.add(method_counter)
        if message.get("id") == expected_id:
            return message


def _stop_app_server(process: subprocess.Popen) -> None:
    if process.stdin is not None:
        try:
            process.stdin.close()
        except OSError:
            pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def _shared_codex_quota_remaining(response: Mapping[str, object]) -> float:
    """Require one unambiguous shared Codex bucket; reject model-specific ambiguity."""
    allowed = response.get("ordinaryUsageAllowed")
    if not isinstance(allowed, bool):
        raise ValueError("Codex account capacity does not report ordinary-usage permission")
    by_id = response.get("rateLimitsByLimitId")
    if by_id is None:
        legacy = response.get("rateLimits")
        if not isinstance(legacy, dict) or legacy.get("limitId") != "codex":
            raise ValueError("Codex account capacity has no identifiable shared Codex bucket")
        buckets = {"codex": legacy}
    elif isinstance(by_id, dict):
        buckets = by_id
    else:
        raise ValueError("Codex account capacity buckets are malformed")
    if "codex" not in buckets:
        raise ValueError("Codex account capacity has no identifiable shared Codex bucket")
    if set(buckets) != {"codex"}:
        raise ValueError("Codex account capacity has model-specific or unknown quota buckets")
    if not isinstance(buckets.get("codex"), dict):
        raise ValueError("Codex account capacity shared bucket is malformed")
    bucket = buckets["codex"]
    if bucket.get("limitId") not in {None, "codex"}:
        raise ValueError("Codex account capacity bucket identity is inconsistent")
    model_slug = bucket.get("normalModelSlug")
    if model_slug is not None and (not isinstance(model_slug, str) or model_slug):
        raise ValueError("Codex account capacity bucket is model-specific")
    windows = [bucket.get(name) for name in ("primary", "secondary")
               if bucket.get(name) is not None]
    if not windows:
        raise ValueError("Codex account capacity has no quota windows")
    remaining = []
    for window in windows:
        if not isinstance(window, dict):
            raise ValueError("Codex account capacity window is malformed")
        used = window.get("usedPercent")
        if (not isinstance(used, int) or isinstance(used, bool) or not 0 <= used <= 100):
            raise ValueError("Codex account capacity window has an invalid usage percentage")
        remaining.append(100 - used)
    return float(min(remaining)) if allowed else 0.0
