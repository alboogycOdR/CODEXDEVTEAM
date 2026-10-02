"""SHA-, command-, and environment-bound test-run evidence cache."""

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping

from .secrets import find_secrets


@dataclass(frozen=True, slots=True)
class TestRunResult:
    name: str
    sha: str
    command: tuple[str, ...]
    passed: bool
    exit_code: int | None
    duration_seconds: float
    log_path: str
    output_sha256: str
    environment_fingerprint: str
    artifact_path: str
    artifact_sha256: str
    cached: bool = False

    @property
    def evidence_ref(self) -> str:
        return (f"testrun:{self.sha}:{self.name}:"
                f"{self.environment_fingerprint}:{self.artifact_sha256}")


class TestRunError(RuntimeError):
    """Raised when a test result cannot be safely tied to a clean SHA."""


class TestRunCache:
    """Run a command once per immutable input key, serializing concurrent callers."""

    def __init__(self, root: str | Path, *, lock_timeout_seconds: float = 1800.0,
                 output_limit_bytes: int = 2_000_000):
        self.root = Path(root).resolve()
        if lock_timeout_seconds <= 0 or output_limit_bytes <= 0:
            raise ValueError("test-run lock timeout and output limit must be positive")
        self.lock_timeout_seconds = lock_timeout_seconds
        self.output_limit_bytes = output_limit_bytes

    def run(self, name: str, command: tuple[str, ...] | list[str], *,
            worktree: str | Path, expected_sha: str, environment: Mapping[str, str],
            timeout_seconds: float) -> TestRunResult:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name or ""):
            raise ValueError("test-run name must be a safe token")
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", expected_sha or ""):
            raise ValueError("expected_sha must be a full Git SHA")
        if (not isinstance(command, (tuple, list)) or not command
                or not all(isinstance(item, str) and item for item in command)):
            raise ValueError("test command must be a non-empty argv sequence")
        if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
            raise ValueError("timeout_seconds must be a positive finite number")
        if not isinstance(environment, Mapping) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in environment.items()
        ):
            raise ValueError("environment must map strings to strings")
        directory = Path(worktree).resolve()
        if not directory.is_dir():
            raise TestRunError("worktree must be an existing directory")
        actual_sha = self._git(directory, "rev-parse", "HEAD").strip().lower()
        sha = expected_sha.lower()
        if actual_sha != sha:
            raise TestRunError(f"expected worktree SHA {sha}, found {actual_sha}")
        if self._git(directory, "status", "--porcelain", "--untracked-files=all").strip():
            raise TestRunError("test-run cache requires a clean worktree")
        env = dict(environment)
        env_hashes = {key: hashlib.sha256(value.encode("utf-8")).hexdigest()
                      for key, value in sorted(env.items())}
        env_fingerprint = self._digest(env_hashes)
        argv = tuple(command)
        command_hash = self.command_fingerprint(argv)
        key_payload = {"name": name, "sha": sha, "command_sha256": command_hash,
                       "environment_fingerprint": env_fingerprint,
                       "timeout_seconds": timeout_seconds}
        key = self._digest(key_payload)
        self.root.mkdir(parents=True, exist_ok=True)
        stem = f"{sha}-{name}-{key[:20]}"
        artifact = self.root / f"{stem}.json"
        log = self.root / f"{stem}.log"
        lock = self.root / f"{stem}.lock"
        if any(path.is_symlink() for path in (artifact, log, lock)):
            raise TestRunError("test-run cache files cannot be symlinks")
        with self._lock(lock):
            cached = self._read(artifact, name, sha, argv, env_fingerprint, log)
            if cached is not None:
                return cached
            started = time.monotonic()
            exit_code: int | None = None
            passed = False
            try:
                completed = subprocess.run(list(argv), cwd=directory, env=env,
                                           capture_output=True, text=True,
                                           encoding="utf-8", errors="replace",
                                           timeout=timeout_seconds, check=False, shell=False)
                exit_code = completed.returncode
                passed = exit_code == 0
                output = (completed.stdout or "") + (completed.stderr or "")
            except subprocess.TimeoutExpired as exc:
                output = self._as_text(exc.stdout) + self._as_text(exc.stderr)
            except OSError as exc:
                output = f"could not start command: {exc}"
            duration = time.monotonic() - started
            if self._git(directory, "rev-parse", "HEAD").strip().lower() != sha:
                passed = False
                output += "\n[cache refused: worktree HEAD changed during the test run]\n"
            safe_output = self._safe_output(output, env, argv)
            encoded = safe_output.encode("utf-8", errors="replace")
            if len(encoded) > self.output_limit_bytes:
                marker = b"\n[output truncated]\n"
                encoded = encoded[:max(0, self.output_limit_bytes - len(marker))] + marker
            log.write_bytes(encoded)
            output_hash = hashlib.sha256(encoded).hexdigest()
            payload = {"name": name, "sha": sha, "command_sha256": command_hash,
                       "passed": passed, "exit_code": exit_code,
                       "duration_seconds": duration, "log_path": str(log),
                       "output_sha256": output_hash,
                       "environment_fingerprint": env_fingerprint,
                       "timeout_seconds": timeout_seconds}
            self._atomic_json(artifact, payload)
            artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
            return TestRunResult(name, sha, argv, passed, exit_code, duration,
                                 str(log), output_hash, env_fingerprint,
                                 str(artifact), artifact_hash)

    def _read(self, artifact: Path, name: str, sha: str, command: tuple[str, ...],
              environment_fingerprint: str, log: Path) -> TestRunResult | None:
        try:
            if artifact.is_symlink() or log.is_symlink():
                return None
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            if (payload.get("name") != name or payload.get("sha") != sha
                    or payload.get("command_sha256") != self.command_fingerprint(command)
                    or payload.get("environment_fingerprint") != environment_fingerprint
                    or Path(payload.get("log_path", "")).resolve() != log.resolve()
                    or not log.is_file()):
                return None
            output_hash = hashlib.sha256(log.read_bytes()).hexdigest()
            if payload.get("output_sha256") != output_hash:
                return None
            artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
            return TestRunResult(name, sha, command, bool(payload["passed"]),
                                 payload.get("exit_code"), float(payload["duration_seconds"]),
                                 str(log), output_hash, environment_fingerprint,
                                 str(artifact), artifact_hash, True)
        except (OSError, ValueError, KeyError, TypeError):
            return None

    @contextmanager
    def _lock(self, path: Path) -> Iterator[None]:
        stream = path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                if path.stat().st_size == 0:
                    stream.seek(0)
                    stream.write(b"0")
                    stream.flush()
                deadline = time.monotonic() + self.lock_timeout_seconds
                while True:
                    try:
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TestRunError("timed out waiting for the test-run cache lock")
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                deadline = time.monotonic() + self.lock_timeout_seconds
                while True:
                    try:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TestRunError("timed out waiting for the test-run cache lock")
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()

    @staticmethod
    def _git(directory: Path, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(directory), *args],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", check=False)
        if result.returncode:
            raise TestRunError(result.stderr.strip() or "Git verification failed")
        return result.stdout

    @staticmethod
    def _digest(value: object) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=False).encode("utf-8")).hexdigest()

    @staticmethod
    def command_fingerprint(command: tuple[str, ...] | list[str]) -> str:
        """Bind evidence to argv without persisting potentially credentialed arguments."""
        return TestRunCache._digest(list(command))

    @staticmethod
    def _safe_output(output: str, environment: Mapping[str, str],
                     command: tuple[str, ...] = ()) -> str:
        if find_secrets(output):
            return "[redacted: secret-like output omitted]\n"
        values = [value for name, value in environment.items()
                  if name != "CODEXDEVTEAM_RUN_SCOPE"]
        values.extend(command[1:])
        for value in sorted(values, key=len, reverse=True):
            if len(value) >= 6:
                output = output.replace(value, "[REDACTED_VALUE]")
        return output

    @staticmethod
    def _as_text(value: str | bytes | None) -> str:
        if value is None:
            return ""
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value

    @staticmethod
    def _atomic_json(path: Path, payload: dict) -> None:
        handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                             dir=path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
        finally:
            Path(temp_name).unlink(missing_ok=True)
