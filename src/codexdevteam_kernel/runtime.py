"""Bounded Codex CLI invocation adapter; model choice remains registry configuration."""

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from .identity import WorkerIdentity
from .registry import WorkerRegistry
from .secrets import find_secrets
from .process_identity import ProcessIdentity, capture_process_identity
from .termination import write_cancellation_receipt


_INVOCATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class InvocationRequest:
    invocation_id: str
    task_id: str | None
    purpose: str
    identity: WorkerIdentity
    prompt: str
    working_directory: str
    timeout_seconds: float = 900.0
    writable: bool = False
    allowed_environment: tuple[str, ...] = ()
    output_limit_chars: int = 200_000
    review_sha: str | None = None
    gate_fingerprint: str | None = None
    reasoning_effort: str | None = None
    state_db_path: str | None = None
    control_outbox_path: str | None = None
    cancel_event: threading.Event | None = None
    cancellation_receipt_dir: str | None = None
    cancellation_token: str | None = field(default=None, repr=False)
    on_process_start: Callable[[ProcessIdentity], None] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not _INVOCATION_ID.fullmatch(self.invocation_id):
            raise ValueError("invocation_id has invalid characters or length")
        if self.task_id is not None and not self.task_id.strip():
            raise ValueError("task_id must be null or non-empty")
        if self.purpose not in {"head", "maker", "checker", "triage"}:
            raise ValueError("unsupported invocation purpose")
        if self.purpose == "maker" and self.task_id is None:
            raise ValueError("maker invocations require an assigned task ID")
        if self.state_db_path is not None and not self.state_db_path.strip():
            raise ValueError("state_db_path must be null or non-empty")
        if self.control_outbox_path is not None and not self.control_outbox_path.strip():
            raise ValueError("control_outbox_path must be null or non-empty")
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError("prompt must be non-empty")
        if self.timeout_seconds <= 0 or self.output_limit_chars <= 0:
            raise ValueError("timeout and output limit must be positive")
        if not isinstance(self.writable, bool):
            raise ValueError("writable must be boolean")
        if any(not isinstance(item, str) or not item for item in self.allowed_environment):
            raise ValueError("allowed_environment entries must be non-empty strings")
        if self.purpose == "checker" and self.writable:
            raise ValueError("checker invocations must be read-only")
        if self.purpose == "checker":
            if not self.review_sha or len(self.review_sha) not in {40, 64}:
                raise ValueError("checker invocations require the reviewed full SHA")
            if not self.gate_fingerprint:
                raise ValueError("checker invocations require the gate fingerprint")
        if self.reasoning_effort is not None and self.reasoning_effort not in {
            "none", "minimal", "low", "medium", "high", "xhigh"
        }:
            raise ValueError("unsupported reasoning effort")
        if self.cancel_event is not None and not isinstance(self.cancel_event, threading.Event):
            raise ValueError("cancel_event must be a threading.Event or null")
        if (self.on_process_start is not None
                and (self.purpose != "maker" or not callable(self.on_process_start))):
            raise ValueError("on_process_start must be a callable for maker invocations")
        if ((self.cancellation_receipt_dir is None) != (self.cancellation_token is None)):
            raise ValueError("cancellation receipt directory and token must be configured together")
        if self.cancellation_receipt_dir is not None:
            if self.purpose != "maker" or not self.cancellation_receipt_dir.strip():
                raise ValueError("durable cancellation receipts are valid only for maker invocations")
            if not isinstance(self.cancellation_token, str) or len(self.cancellation_token) < 32:
                raise ValueError("cancellation token is invalid")


@dataclass(frozen=True, slots=True)
class InvocationResult:
    invocation_id: str
    task_id: str | None
    purpose: str
    unit_id: str
    runtime: str
    model: str
    status: str
    exit_code: int | None
    duration_seconds: float
    stdout: str
    stderr: str
    truncated: bool
    output_sha256: str
    started_at: float
    finished_at: float
    review_sha: str | None = None
    gate_fingerprint: str | None = None
    role: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reported_cost_usd: float | None = None
    memory_injection_event_id: str | None = None
    process_tree_cancel_method: str | None = None
    process_tree_cancel_verified: bool | None = None
    process_tree_cancel_exit_code: int | None = None

    def __post_init__(self) -> None:
        if not _INVOCATION_ID.fullmatch(self.invocation_id):
            raise ValueError("invalid invocation_id")
        if self.status not in {"succeeded", "failed", "timed_out", "launch_failed",
                               "termination_unverified"}:
            raise ValueError("invalid invocation status")
        if ((self.process_tree_cancel_method is None)
                != (self.process_tree_cancel_verified is None)):
            raise ValueError("process-tree cancellation evidence must include method and result")
        if (self.process_tree_cancel_method is not None
                and self.process_tree_cancel_method not in {
                    "windows_taskkill_tree", "posix_process_group",
                    "runtime_adapter_failure",
                }):
            raise ValueError("unsupported process-tree cancellation method")
        if self.process_tree_cancel_verified is not None and not isinstance(
                self.process_tree_cancel_verified, bool):
            raise ValueError("process-tree cancellation result must be boolean")
        if self.process_tree_cancel_exit_code is not None and (
                not isinstance(self.process_tree_cancel_exit_code, int)
                or isinstance(self.process_tree_cancel_exit_code, bool)):
            raise ValueError("process-tree cancellation exit code must be an integer or null")
        if (self.status == "termination_unverified"
                and self.process_tree_cancel_verified is not False):
            raise ValueError("termination_unverified requires failed cancellation evidence")
        if (not self.unit_id.strip() or not self.runtime.strip() or not self.model.strip()
                or not re.fullmatch(r"[0-9a-f]{64}", self.output_sha256)):
            raise ValueError("invocation identity and output SHA-256 are required")
        if self.duration_seconds < 0 or self.finished_at < self.started_at:
            raise ValueError("invocation timing values are invalid")
        if self.role is not None and (not isinstance(self.role, str) or not self.role.strip()):
            raise ValueError("role must be null or non-empty")
        if (self.memory_injection_event_id is not None
                and (not isinstance(self.memory_injection_event_id, str)
                     or not self.memory_injection_event_id.strip())):
            raise ValueError("memory_injection_event_id must be null or non-empty")
        for name in ("input_tokens", "output_tokens", "cached_input_tokens"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or null")
        if (self.reported_cost_usd is not None
                and (not isinstance(self.reported_cost_usd, (int, float))
                     or isinstance(self.reported_cost_usd, bool)
                     or not math.isfinite(self.reported_cost_usd)
                     or self.reported_cost_usd < 0)):
            raise ValueError("reported_cost_usd must be finite and non-negative or null")
        if self.status == "succeeded" and self.exit_code != 0:
            raise ValueError("succeeded invocation must have exit code zero")
        if self.purpose == "checker" and (
            not self.review_sha or not re.fullmatch(r"[0-9a-fA-F]{40,64}", self.review_sha)
            or not self.gate_fingerprint
        ):
            raise ValueError("checker receipt must bind the reviewed SHA and gate fingerprint")


class CodexExecAdapter:
    """Run `codex exec` without a shell, with explicit model and sandbox policy."""

    BASE_ENVIRONMENT = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR",
                        "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "CODEX_HOME")

    def __init__(self, executable: str = "codex"):
        if not executable.strip():
            raise ValueError("Codex executable must be non-empty")
        self.executable = executable

    def invoke(self, request: InvocationRequest) -> InvocationResult:
        if request.identity.runtime != "codex":
            raise ValueError("CodexExecAdapter requires runtime='codex'")
        cwd = Path(request.working_directory).resolve()
        if not cwd.is_dir():
            raise ValueError("working_directory must be an existing directory")
        sandbox = "workspace-write" if request.writable else "read-only"
        prompt = request.prompt
        if request.purpose == "checker":
            prompt += ("\n\nCODEXDEVTEAM REVIEW BINDING\n"
                       f"Task: {request.task_id}\nCommit SHA: {request.review_sha}\n"
                       f"Gate fingerprint: {request.gate_fingerprint}\n"
                       "Provide a verdict for exactly this committed task state.")
        argv = [self.executable, "exec", "--json", "--ephemeral", "--model",
                request.identity.model, "--sandbox", sandbox, "--cd", str(cwd), "-"]
        if request.reasoning_effort:
            argv[2:2] = ["-c", f'model_reasoning_effort="{request.reasoning_effort}"']
        names = set(self.BASE_ENVIRONMENT) | set(request.allowed_environment)
        env = {name: os.environ[name] for name in names if name in os.environ}
        if request.purpose == "maker":
            env.update({
                "CODEXDEVTEAM_WORKER_ID": request.identity.unit_id,
                "CODEXDEVTEAM_WORKER_ROLE": request.identity.role,
                "CODEXDEVTEAM_CAPABILITY_FLOOR": request.identity.capability_floor,
                "CODEXDEVTEAM_RUNTIME": request.identity.runtime,
                "CODEXDEVTEAM_MODEL": request.identity.model,
                "CODEXDEVTEAM_TASK_ID": request.task_id or "",
            })
            if request.state_db_path:
                database = Path(request.state_db_path)
                if database.is_symlink():
                    raise ValueError("state_db_path cannot be a symlink")
                database = database.resolve(strict=True)
                if not database.is_file():
                    raise ValueError("state_db_path must name a regular file")
                env["CODEXDEVTEAM_STATE_DB"] = str(database)
            if request.control_outbox_path:
                outbox = Path(request.control_outbox_path)
                if outbox.is_symlink():
                    raise ValueError("CONTROL outbox cannot be a symlink")
                outbox.mkdir(parents=True, exist_ok=True)
                outbox = outbox.resolve(strict=True)
                if not outbox.is_dir() or not outbox.is_relative_to(cwd):
                    raise ValueError("CONTROL outbox must be inside the maker worktree")
                env["CODEXDEVTEAM_CONTROL_OUTBOX"] = str(outbox)
        runtime_temp = tempfile.TemporaryDirectory(prefix="codexdevteam-invocation-")
        isolated_temp = str(Path(runtime_temp.name).resolve(strict=True))
        env.update({"TEMP": isolated_temp, "TMP": isolated_temp, "TMPDIR": isolated_temp})
        started_at = time.time()
        started = time.monotonic()
        status, exit_code, stdout, stderr = "failed", None, "", ""
        cancel_method = None
        cancel_verified = None
        cancel_exit_code = None
        try:
            try:
                (exit_code, stdout, stderr, cancel_method, cancel_verified,
                 cancel_exit_code) = _run_invocation_process(argv, prompt, cwd, env, request)
                if cancel_verified is False:
                    status = "termination_unverified"
                    stderr = (stderr + "\nWindows invocation containment cleanup was not verified.").strip()
                else:
                    status = "succeeded" if exit_code == 0 else "failed"
            except subprocess.TimeoutExpired as exc:
                verified = getattr(exc, "process_tree_cancel_verified", None)
                status = "timed_out" if verified is True else "termination_unverified"
                stdout = _as_text(exc.stdout)
                stderr = _as_text(exc.stderr)
                cancel_method = getattr(exc, "process_tree_cancel_method", None)
                cancel_exit_code = getattr(exc, "process_tree_cancel_exit_code", None)
                cancel_verified = verified
            except _InvocationCancelled as exc:
                status = ("failed" if exc.verified else
                          "termination_unverified")
                exit_code = None
                stdout = ""
                stderr = ("Invocation cancelled because the HEAD lease was lost."
                          if exc.verified else
                          "Invocation cancellation did not verify process-tree termination.")
                cancel_method = exc.method
                cancel_verified = exc.verified
                cancel_exit_code = exc.exit_code
            except _ProcessIdentityPersistenceError as exc:
                status = "failed" if exc.verified else "termination_unverified"
                stderr = "Maker process identity could not be recorded under the HEAD lease."
                cancel_method = exc.method
                cancel_verified = exc.verified
                cancel_exit_code = exc.exit_code
            except OSError as exc:
                status = "launch_failed"
                stderr = str(exc)
        finally:
            runtime_temp.cleanup()
        duration = time.monotonic() - started
        stdout = _redact(stdout)
        stderr = _redact(stderr)
        usage = _codex_usage(stdout) if request.identity.runtime == "codex" else None
        output_digest = hashlib.sha256((stdout + stderr).encode("utf-8", errors="replace")).hexdigest()
        combined_length = len(stdout) + len(stderr)
        truncated = combined_length > request.output_limit_chars
        if truncated:
            remaining = request.output_limit_chars
            stdout = stdout[:remaining]
            remaining -= len(stdout)
            stderr = stderr[:max(remaining, 0)]
        return InvocationResult(request.invocation_id, request.task_id, request.purpose,
                                request.identity.unit_id, request.identity.runtime,
                                request.identity.model, status, exit_code, duration,
                                stdout, stderr, truncated, output_digest,
                                started_at, time.time(), request.review_sha,
                                request.gate_fingerprint, role=request.identity.role,
                                input_tokens=usage[0] if usage else None,
                                output_tokens=usage[1] if usage else None,
                                cached_input_tokens=usage[2] if usage else None,
                                process_tree_cancel_method=cancel_method,
                                process_tree_cancel_verified=cancel_verified,
                                process_tree_cancel_exit_code=cancel_exit_code)


class _InvocationCancelled(Exception):
    """Raised after a cancelled runtime process tree has been reaped."""

    def __init__(self, method: str, verified: bool, exit_code: int | None):
        super().__init__("invocation cancelled")
        self.method = method
        self.verified = verified
        self.exit_code = exit_code


class _ProcessIdentityPersistenceError(Exception):
    """A spawned maker lacked durable identity and had to be terminated."""

    def __init__(self, method: str, verified: bool, exit_code: int | None):
        super().__init__("maker process identity could not be persisted")
        self.method = method
        self.verified = verified
        self.exit_code = exit_code


def _run_invocation_process(argv: list[str], prompt: str, cwd: Path,
                            env: dict[str, str], request: InvocationRequest
                            ) -> tuple[int, str, str, str | None, bool | None, int | None]:
    windows = os.name == "nt"
    job = None
    kwargs = {
        "cwd": cwd, "env": env, "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
        "text": True, "encoding": "utf-8", "errors": "replace", "shell": False,
    }
    if windows:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if request.purpose == "maker" and request.on_process_start is not None:
            from .windows_jobs import WindowsJob
            job = WindowsJob.create()
            kwargs["creationflags"] |= 0x00000004  # CREATE_SUSPENDED until fenced and recorded.
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(argv, **kwargs)
    except Exception:
        if job is not None:
            job.close()
        raise
    if job is not None:
        process._codexdevteam_job = job
        try:
            job.assign(process)
        except Exception as exc:
            verified = _terminate_unassigned_suspended_process(process)
            _communicate_after_termination(process)
            job.close()
            raise _ProcessIdentityPersistenceError(
                "windows_job_assignment", verified, process.poll()) from exc
    if request.on_process_start is not None:
        try:
            identity = capture_process_identity(process.pid)
            if job is not None:
                identity = replace(identity, containment_ref=job.name)
            request.on_process_start(identity)
        except Exception as exc:
            method, verified, exit_code = _terminate_process_tree(process)
            verified = _publish_cancellation_receipt(request, method, verified, exit_code)
            _communicate_after_termination(process)
            if job is not None:
                job.close()
            raise _ProcessIdentityPersistenceError(method, verified, exit_code) from exc
    if job is not None:
        try:
            job.resume(process)
        except Exception as exc:
            method, verified, exit_code = _terminate_process_tree(process)
            _communicate_after_termination(process)
            job.close()
            raise _ProcessIdentityPersistenceError(method, verified, exit_code) from exc

    try:
        deadline = time.monotonic() + request.timeout_seconds
        first = True
        while True:
            if request.cancel_event is not None and request.cancel_event.is_set():
                method, verified, exit_code = _terminate_process_tree(process)
                verified = _publish_cancellation_receipt(request, method, verified, exit_code)
                _communicate_after_termination(process)
                raise _InvocationCancelled(method, verified, exit_code)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                method, verified, cancel_exit_code = _terminate_process_tree(process)
                verified = _publish_cancellation_receipt(
                    request, method, verified, cancel_exit_code)
                stdout, stderr = _communicate_after_termination(process)
                exc = subprocess.TimeoutExpired(argv, request.timeout_seconds,
                                                output=stdout, stderr=stderr)
                exc.process_tree_cancel_method = method
                exc.process_tree_cancel_verified = verified
                exc.process_tree_cancel_exit_code = cancel_exit_code
                raise exc
            wait_for = remaining if request.cancel_event is None else min(remaining, 0.2)
            try:
                stdout, stderr = process.communicate(input=prompt if first else None,
                                                     timeout=wait_for)
                cleanup_method = None
                cleanup_verified = None
                cleanup_exit_code = None
                if job is not None:
                    try:
                        active_processes = job.active_process_count()
                    except OSError:
                        active_processes = -1
                    if active_processes != 0:
                        cleanup_method = "windows_job_object"
                        cleanup_verified = (active_processes > 0
                                            and job.terminate_and_verify())
                        cleanup_exit_code = process.poll()
                    close_verified = job.close()
                    job = None
                    if not close_verified and cleanup_verified is not True:
                        cleanup_method = "windows_job_object_close"
                        cleanup_verified = False
                return (process.returncode, stdout or "", stderr or "", cleanup_method,
                        cleanup_verified, cleanup_exit_code)
            except subprocess.TimeoutExpired:
                first = False
                continue
    finally:
        if job is not None:
            job.close()


def _publish_cancellation_receipt(request: InvocationRequest, method: str,
                                  verified: bool, exit_code: int | None) -> bool:
    if not verified or request.cancellation_receipt_dir is None:
        return verified
    try:
        write_cancellation_receipt(
            request.cancellation_receipt_dir,
            token=request.cancellation_token or "",
            invocation_id=request.invocation_id,
            task_id=request.task_id or "",
            method=method,
            exit_code=exit_code,
            observed_at=time.time(),
        )
        return True
    except (OSError, ValueError):
        # A kill without a durable, verifiable receipt cannot release task liveness.
        return False


def _communicate_after_termination(process: subprocess.Popen) -> tuple[str | None, str | None]:
    """Collect output with a bound even when an unverified descendant holds a pipe."""
    try:
        return process.communicate(timeout=5)
    except subprocess.TimeoutExpired as exc:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        return _as_text(exc.output), _as_text(exc.stderr)


def _terminate_process_tree(process: subprocess.Popen) -> tuple[str, bool, int | None]:
    """Terminate an invocation tree and report bounded, observable evidence."""
    if os.name == "nt":
        job = getattr(process, "_codexdevteam_job", None)
        if job is not None:
            verified = job.terminate_and_verify(timeout_seconds=10)
            return "windows_job_object", verified, process.poll()
        return_code = None
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, text=True, timeout=5, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return_code = result.returncode
        except (OSError, subprocess.TimeoutExpired):
            return "windows_taskkill_tree", False, None
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return "windows_taskkill_tree", False, return_code
        return "windows_taskkill_tree", return_code == 0 and process.poll() is not None, return_code

    import signal

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return "posix_process_group", False, None
        return "posix_process_group", True, None
    except OSError:
        return "posix_process_group", False, None
    try:
        process.wait(timeout=0.25)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return "posix_process_group", False, None
        return "posix_process_group", True, None
    except OSError:
        return "posix_process_group", False, None
    try:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return "posix_process_group", False, None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return "posix_process_group", True, None
        except OSError:
            return "posix_process_group", False, None
        time.sleep(0.02)
    return "posix_process_group", False, None


def _terminate_unassigned_suspended_process(process: subprocess.Popen) -> bool:
    """Kill the one never-resumed process that could not join its Job Object."""
    try:
        process.kill()
        process.wait(timeout=5)
        return process.poll() is not None
    except (OSError, subprocess.TimeoutExpired):
        return False


def request_for_worker(registry: WorkerRegistry, worker_id: str, *, invocation_id: str,
                       task_id: str | None, purpose: str, prompt: str,
                       working_directory: str, timeout_seconds: float = 900.0,
                       writable: bool = False, review_sha: str | None = None,
                       gate_fingerprint: str | None = None,
                       allowed_environment: tuple[str, ...] = (),
                       state_db_path: str | None = None,
                       control_outbox_path: str | None = None,
                       cancellation_receipt_dir: str | None = None,
                       cancellation_token: str | None = None,
                       cancel_event: threading.Event | None = None,
                       on_process_start: Callable[[ProcessIdentity], None] | None = None
                       ) -> InvocationRequest:
    """Build a runtime request from registry identity and centralized role policy."""
    worker = registry.resolve(worker_id)
    policy = registry.policy_for_role(worker.identity.role)
    return InvocationRequest(
        invocation_id=invocation_id, task_id=task_id, purpose=purpose,
        identity=worker.identity, prompt=prompt, working_directory=working_directory,
        timeout_seconds=timeout_seconds, writable=writable,
        allowed_environment=allowed_environment, review_sha=review_sha,
        gate_fingerprint=gate_fingerprint, state_db_path=state_db_path,
        control_outbox_path=control_outbox_path,
        cancellation_receipt_dir=cancellation_receipt_dir,
        cancellation_token=cancellation_token,
        cancel_event=cancel_event,
        on_process_start=on_process_start,
        reasoning_effort=policy.reasoning_effort if policy else None,
    )


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _redact(value: str) -> str:
    return "[redacted: secret-like output omitted]\n" if find_secrets(value) else value


def _codex_usage(output: str) -> tuple[int, int, int] | None:
    """Extract reported per-turn usage from Codex JSONL; malformed lines are ignored."""
    totals = [0, 0, 0]
    observed = False
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(event, dict) or event.get("type") != "turn.completed":
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        input_count = usage.get("input_tokens")
        output_count = usage.get("output_tokens")
        details = usage.get("input_tokens_details")
        cached = (details.get("cached_tokens") if isinstance(details, dict)
                  else usage.get("cached_input_tokens", 0))
        counts = (input_count, output_count, cached)
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0
               for value in counts):
            continue
        totals = [left + right for left, right in zip(totals, counts)]
        observed = True
    return tuple(totals) if observed else None
