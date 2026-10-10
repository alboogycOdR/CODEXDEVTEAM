"""Deterministic pre-review checks with SHA- and configuration-bound caching."""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .protocol import TaskRecord
from .secrets import find_secrets
from .test_runs import TestRunCache, TestRunResult
from .tasks import TaskState
from .territory import decide_write, normalize_repo_path


@dataclass(frozen=True, slots=True)
class CheckResult:
    status: str
    summary: str
    duration_seconds: float = 0.0
    log_path: str | None = None
    output_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class GateResult:
    task_id: str
    sha: str
    status: str
    checks: Mapping[str, CheckResult]
    fingerprint: str
    artifact_path: str
    cached: bool = False
    new_failures: tuple[str, ...] = ()
    base_failures: tuple[str, ...] = ()
    inherited_failures: tuple[str, ...] = ()
    failure_owner_ids: tuple[str, ...] = ()
    open_failure_owner_ids: tuple[str, ...] = ()
    test_run_result: TestRunResult | None = field(default=None, repr=False, compare=False)


class GateRunner:
    """Run configured argv checks without a shell or model-runtime dependency."""

    ESSENTIAL_ENV = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")

    def __init__(self, repository: str | Path, artifact_root: str | Path | None = None, *,
                 protected_paths: tuple[str, ...] = ("PLAN.md", ".codexdevteam/**")):
        self.repository = Path(repository).resolve()
        self.protected_paths = tuple(normalize_repo_path(path) for path in protected_paths)
        self.artifact_root = Path(artifact_root).resolve() if artifact_root else (
            self.repository / ".codexdevteam" / "gates"
        )
        self.test_run_cache = TestRunCache(
            self._git_common_dir(self.repository) / "codexdevteam" / "testruns")

    def run_test_command(self, task: TaskRecord, command: tuple[str, ...] | list[str], *,
                         worktree: str | Path, expected_sha: str,
                         allowed_environment: tuple[str, ...] = (),
                         per_worktree_environment: Mapping[str, str] | None = None,
                         timeout_seconds: float = 1800.0,
                         name: str = "test_full") -> TestRunResult:
        """Run or reuse SHA-bound task test evidence with the gate's exact env policy."""
        worktree_path = Path(worktree).resolve()
        if self._git_common_dir(worktree_path) != self._git_common_dir(self.repository):
            raise ValueError("test command worktree belongs to another Git repository")
        env = self._scrubbed_environment(allowed_environment)
        env.update(self.materialize_worktree_environment(
            per_worktree_environment or {}, task=task, worktree=worktree_path,
            run_scope="task"))
        return self.test_run_cache.run(name, command, worktree=worktree_path,
                                       expected_sha=expected_sha, environment=env,
                                       timeout_seconds=timeout_seconds)

    def run(self, task: TaskRecord, worktree: str | Path, *, expected_sha: str,
            base_ref: str | None,
            commands: Mapping[str, tuple[str, ...] | list[str] | None],
            allowed_environment: tuple[str, ...] = (),
            per_worktree_environment: Mapping[str, str] | None = None,
            timeout_seconds: float = 1800.0,
            refresh: bool = False, open_task_ids: tuple[str, ...] = (),
            active_tasks: tuple[TaskRecord, ...] | None = None) -> GateResult:
        worktree_path = Path(worktree).resolve()
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", expected_sha or ""):
            raise ValueError("expected_sha must be a full Git SHA")
        expected_sha = expected_sha.lower()
        if any(not isinstance(name, str) or not name for name in allowed_environment):
            raise ValueError("allowed_environment entries must be non-empty strings")
        if not isinstance(open_task_ids, tuple) or any(
            not re.fullmatch(r"TASK-[A-Z0-9][A-Z0-9-]*", task_id) for task_id in open_task_ids
        ):
            raise ValueError("open_task_ids must contain task IDs")
        if (not isinstance(active_tasks, tuple)
                or not all(isinstance(item, TaskRecord) for item in active_tasks)):
            raise ValueError("active_tasks must be the authoritative task snapshot tuple")
        active_ids = [item.task_id for item in active_tasks]
        if len(active_ids) != len(set(active_ids)):
            raise ValueError("active task snapshot contains duplicate task IDs")
        current_tasks = [item for item in active_tasks if item.task_id == task.task_id]
        if current_tasks != [task]:
            raise ValueError("active task snapshot must contain the exact gate task once")
        check_names = ("build", "typecheck", "test_full", "reachability", "mutation")
        unknown_checks = set(commands) - set(check_names)
        if unknown_checks:
            raise ValueError(f"unknown mechanical checks: {', '.join(sorted(unknown_checks))}")
        for name in check_names:
            command = commands.get(name)
            if command is not None and (isinstance(command, str) or not isinstance(command, (tuple, list))):
                raise ValueError(f"{name} command must be a list of argv strings")
            if command is not None and (not command or not all(isinstance(arg, str) and arg for arg in command)):
                raise ValueError(f"{name} command must be a non-empty list of argv strings")

        templates = dict(per_worktree_environment or {})
        env = self._scrubbed_environment(allowed_environment)
        env.update(self.materialize_worktree_environment(
            templates, task=task, worktree=worktree_path, run_scope="task"))
        checks: dict[str, CheckResult] = {}
        try:
            actual_sha = self._git(worktree_path, "rev-parse", "HEAD").strip()
        except RuntimeError as exc:
            checks["sha"] = CheckResult("failed", f"could not read worktree HEAD: {exc}")
            return self._failure_without_cache(task, expected_sha, checks, commands)
        if actual_sha != expected_sha:
            checks["sha"] = CheckResult("failed", f"expected {expected_sha}, found {actual_sha}")
            return self._failure_without_cache(task, expected_sha, checks, commands)
        checks["sha"] = CheckResult("passed", f"worktree HEAD is {actual_sha}")
        dirty = self._git(worktree_path, "status", "--porcelain", "--untracked-files=all").strip()
        if dirty:
            checks["clean_worktree"] = CheckResult("failed", "worktree has uncommitted or untracked changes")
            return self._failure_without_cache(task, expected_sha, checks, commands)
        checks["clean_worktree"] = CheckResult("passed", "worktree has no uncommitted or untracked changes")
        if base_ref is None:
            checks["territory"] = CheckResult("failed", "base_ref is required for territory verification")
            return self._failure_without_cache(task, expected_sha, checks, commands)
        try:
            base_sha = self._git(worktree_path, "merge-base", base_ref, expected_sha).strip()
        except RuntimeError as exc:
            checks["territory"] = CheckResult("failed", f"could not resolve base ref: {exc}")
            return self._failure_without_cache(task, expected_sha, checks, commands)

        fingerprint = self._fingerprint(task, expected_sha, base_sha, commands,
                                        allowed_environment, env, timeout_seconds, open_task_ids,
                                        templates, self.protected_paths, active_tasks)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        if worktree_path == self.artifact_root or worktree_path in self.artifact_root.parents:
            raise ValueError("gate artifacts must be stored outside the task worktree")
        artifact_path = self.artifact_root / f"{task.task_id}-{expected_sha}-{fingerprint[:16]}.json"
        if artifact_path.exists() and not refresh:
            cached = self._load_cached(artifact_path, task.task_id, expected_sha, fingerprint)
            if cached:
                test_run_result = None
                if commands.get("test_full"):
                    test_run_result = self.run_test_command(
                        task, tuple(commands["test_full"]), worktree=worktree_path,
                        expected_sha=expected_sha, allowed_environment=allowed_environment,
                        per_worktree_environment=templates,
                        timeout_seconds=timeout_seconds)
                if test_run_result is None or test_run_result.passed:
                    return GateResult(cached.task_id, cached.sha, cached.status, cached.checks,
                                      cached.fingerprint, cached.artifact_path, True,
                                      cached.new_failures, cached.base_failures,
                                      cached.inherited_failures, cached.failure_owner_ids,
                                      cached.open_failure_owner_ids, test_run_result)

        test_run_result = None
        checks["territory"] = self._territory_check(
            task, active_tasks, worktree_path, base_sha, expected_sha)
        new_failures: tuple[str, ...] = ()
        base_failures: tuple[str, ...] = ()
        inherited_failures: tuple[str, ...] = ()
        if checks["territory"].status == "failed":
            self._skip_commands(checks, commands, "territory verification failed")
            checks["secret_scan"] = CheckResult("skipped", "territory verification failed")
            checks["baseline_analysis"] = CheckResult("skipped", "territory verification failed")
        else:
            checks["secret_scan"] = self._secret_check(worktree_path, expected_sha,
                                                       base_sha)
            for name in check_names:
                argv = commands.get(name)
                if not argv:
                    checks[name] = CheckResult("skipped", "no command configured")
                elif name == "test_full":
                    test_result = self.run_test_command(
                        task, tuple(argv), worktree=worktree_path,
                        expected_sha=expected_sha, allowed_environment=allowed_environment,
                        per_worktree_environment=templates,
                        timeout_seconds=timeout_seconds)
                    test_run_result = test_result
                    checks[name] = CheckResult(
                        "passed" if test_result.passed else "failed",
                        "exit code " + str(test_result.exit_code),
                        test_result.duration_seconds, test_result.log_path,
                        test_result.output_sha256)
                else:
                    checks[name] = self._run_command(name, tuple(argv), worktree_path, env,
                                                     timeout_seconds)
            baseline_checks = self._baseline_checks(base_sha, commands, env, timeout_seconds,
                                                    task=task, templates=templates)
            checks.update(baseline_checks)
            new_failures, base_failures, inherited_failures = self._compare_baseline(checks)
            owned_inherited = bool(set(task.owns_failures) & set(open_task_ids))
            baseline_unavailable = any(
                check.status == "failed" and check.log_path is None
                for name, check in checks.items() if name.startswith("base_")
                and commands.get(name.removeprefix("base_"))
            ) or ("baseline_cleanup" in checks and checks["baseline_cleanup"].status == "failed")
            checks["baseline_analysis"] = CheckResult(
                "failed" if new_failures or (inherited_failures and not owned_inherited)
                or baseline_unavailable else "passed",
                self._baseline_summary(new_failures, base_failures, inherited_failures,
                                       task.owns_failures, open_task_ids, baseline_unavailable))
        missing_required = [name for name in ("build", "typecheck", "test_full")
                            if not commands.get(name)]
        required_failed = any(checks[name].status != "passed"
                              for name in ("sha", "clean_worktree", "territory", "secret_scan",
                                           "baseline_analysis"))
        inherited = set(inherited_failures)
        required_failed = required_failed or any(
            checks[name].status != "passed" and name not in inherited
            for name in ("build", "typecheck", "test_full"))
        optional_failed = any(commands.get(name) and checks[name].status != "passed"
                              and name not in inherited for name in ("reachability", "mutation"))
        baseline_failed = checks.get("baseline_analysis", CheckResult("failed", "missing baseline analysis")).status != "passed"
        status = "failed" if required_failed or optional_failed or missing_required or baseline_failed else "passed"
        if missing_required:
            checks["configuration"] = CheckResult(
                "failed", "required commands missing: " + ", ".join(missing_required))

        result = GateResult(task.task_id, expected_sha, status, checks, fingerprint,
                            str(artifact_path), False, new_failures, base_failures,
                            inherited_failures, task.owns_failures,
                            tuple(sorted(set(task.owns_failures) & set(open_task_ids))),
                            test_run_result)
        self._save_result(artifact_path, result)
        return result

    def _scrubbed_environment(self, allowed: tuple[str, ...]) -> dict[str, str]:
        names = set(self.ESSENTIAL_ENV) | set(allowed)
        return {name: os.environ[name] for name in names if name in os.environ}

    @classmethod
    def materialize_worktree_environment(cls, templates: Mapping[str, str], *,
                                         task: TaskRecord, worktree: Path,
                                         run_scope: str) -> dict[str, str]:
        """Expand isolated test-resource templates without inheriting ambient values."""
        if not isinstance(run_scope, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", run_scope):
            raise ValueError("run_scope must be a safe token")
        values = {"task_id": task.task_id,
                  "worker_id": task.assigned_worker or "unassigned",
                  "unit": task.assigned_worker or "unassigned",
                  "worktree_name": worktree.name,
                  "run_scope": run_scope}
        result: dict[str, str] = {"CODEXDEVTEAM_RUN_SCOPE": run_scope}
        for name, template in templates.items():
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                    or name.upper() in {item.upper() for item in cls.ESSENTIAL_ENV}
                    or name == "CODEXDEVTEAM_RUN_SCOPE"):
                raise ValueError(f"invalid or protected per-worktree environment name: {name}")
            if not isinstance(template, str) or not template:
                raise ValueError(f"per-worktree environment template for {name} must be non-empty text")
            tokens = re.findall(r"\{([^{}]*)\}", template)
            unknown = set(tokens) - set(values)
            if unknown or template.count("{") != template.count("}"):
                raise ValueError(f"unsupported template token for {name}: {', '.join(sorted(unknown))}")
            result[name] = template.format_map(values)
        return result

    def _territory_check(self, task: TaskRecord, active_tasks: tuple[TaskRecord, ...], worktree: Path,
                         base_sha: str, head_sha: str) -> CheckResult:
        try:
            changed = self._git(worktree, "diff", "--name-only", "--no-renames",
                                base_sha, head_sha)
        except RuntimeError as exc:
            return CheckResult("failed", f"could not compare task diff to base: {exc}")
        violations = []
        root = worktree.resolve()
        for raw in changed.splitlines():
            path = raw.strip().replace("\\", "/")
            if not path:
                continue
            try:
                normalized = normalize_repo_path(path)
                resolved = (root / Path(*normalized.split("/"))).resolve(strict=False)
                if not resolved.is_relative_to(root):
                    violations.append(f"{normalized} (resolves outside repository)")
                    continue
                if normalized == "PLAN.md":
                    violations.append("PLAN.md (controlled through HEAD)")
                    continue
                conflicting_owners = [
                    other.task_id for other in active_tasks
                    if other.task_id != task.task_id
                    and other.state in {TaskState.CLAIMED, TaskState.IN_PROGRESS,
                                        TaskState.NEEDS_REVIEW}
                    and other.assigned_worker
                    and decide_write(normalized, other.owned_paths).allowed
                ]
                if conflicting_owners:
                    violations.append(
                        f"{normalized} (reserved by active task "
                        + ", ".join(sorted(conflicting_owners)) + ")")
                    continue
                ownership = decide_write(normalized, task.owned_paths)
                grant = decide_write(normalized, task.protected_grants)
                protected = decide_write(normalized, (), protected_paths=self.protected_paths)
                if protected.reason == "protected path" and not grant.allowed:
                    violations.append(f"{normalized} (protected path)")
                elif not ownership.allowed:
                    violations.append(normalized)
            except (OSError, ValueError, RuntimeError):
                violations.append(f"{path} (invalid or unsafe path)")
        if violations:
            return CheckResult("failed", "paths outside task territory: " + ", ".join(violations))
        return CheckResult("passed", f"{len(changed.splitlines())} changed paths are within Owned_Paths")

    def _secret_check(self, worktree: Path, head_sha: str, base_sha: str) -> CheckResult:
        try:
            changed = self._git(worktree, "diff", "--name-only", base_sha, head_sha)
        except RuntimeError as exc:
            return CheckResult("failed", f"could not enumerate changed paths for secret scan: {exc}")
        findings: list[str] = []
        for raw in changed.splitlines():
            path = raw.strip().replace("\\", "/")
            if not path:
                continue
            try:
                blob = subprocess.run(["git", "-C", str(worktree), "show", f"{head_sha}:{path}"],
                                      capture_output=True, timeout=20, check=False)
            except (OSError, subprocess.TimeoutExpired):
                return CheckResult("failed", f"could not inspect changed path for secrets: {path}")
            if blob.returncode:
                continue  # Deleted path has no new content to scan.
            if b"\0" in blob.stdout:
                continue  # Binary data is outside text credential-pattern scanning.
            labels = find_secrets(blob.stdout.decode("utf-8", errors="replace"))
            if labels:
                findings.append(f"{path}: {', '.join(labels)}")
        if findings:
            return CheckResult("failed", "credential patterns found: " + "; ".join(findings))
        return CheckResult("passed", "no configured credential patterns found in changed text files")

    def _baseline_checks(self, base_sha: str, commands: Mapping,
                         env: dict[str, str], timeout_seconds: float, *,
                         task: TaskRecord,
                         templates: Mapping[str, str]) -> dict[str, CheckResult]:
        names = ("build", "typecheck", "test_full", "reachability", "mutation")
        configured = [name for name in names if commands.get(name)]
        if not configured:
            return {f"base_{name}": CheckResult("skipped", "no command configured")
                    for name in names}
        temporary = tempfile.TemporaryDirectory(
            prefix="codexdevteam-baseline-", ignore_cleanup_errors=True)
        with temporary as temp_root:
            baseline = Path(temp_root) / "checkout"
            added = subprocess.run(["git", "-C", str(self.repository), "worktree", "add",
                                    "--detach", str(baseline), base_sha], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=60)
            if added.returncode:
                detail = added.stderr.strip() or "git worktree add failed"
                return {f"base_{name}": (CheckResult("failed", detail) if name in configured
                                          else CheckResult("skipped", "no command configured"))
                        for name in names}
            results: dict[str, CheckResult] = {}
            try:
                baseline_env = dict(env)
                for name, template_value in self.materialize_worktree_environment(
                    templates, task=task, worktree=baseline, run_scope="baseline").items():
                    baseline_env[name] = template_value
                for name in names:
                    argv = commands.get(name)
                    results[f"base_{name}"] = (
                        CheckResult("skipped", "no command configured") if not argv else
                        self._run_command(f"base_{name}", tuple(argv), baseline,
                                          baseline_env, timeout_seconds))
            finally:
                results["baseline_cleanup"] = self._cleanup_baseline(baseline, temporary)
            return results

    def _cleanup_baseline(self, baseline: Path,
                          temporary: tempfile.TemporaryDirectory) -> CheckResult:
        # This fallback owns only the newly allocated temporary root. Never prune
        # the repository or infer cleanup from the first Git command's exit code.
        if baseline != Path(temporary.name) / "checkout":
            return CheckResult("failed", "baseline cleanup path does not match temporary root")
        try:
            subprocess.run(["git", "-C", str(self.repository), "worktree", "remove",
                            "--force", str(baseline)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            pass  # Positive absence checks below are authoritative.
        try:
            temporary.cleanup()
        except OSError:
            pass
        try:
            listed = subprocess.run(
                ["git", "-C", str(self.repository), "worktree", "list", "--porcelain", "-z"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return CheckResult("failed", "could not verify baseline worktree registration cleanup")
        if listed.returncode:
            return CheckResult("failed", "could not verify baseline worktree registration cleanup")
        expected = os.path.normcase(os.path.abspath(baseline))
        registered = any(
            os.path.normcase(os.path.abspath(record[len("worktree "):])) == expected
            for record in listed.stdout.split("\0") if record.startswith("worktree "))
        if os.path.lexists(baseline) or os.path.lexists(temporary.name) or registered:
            return CheckResult("failed", "baseline files or worktree registration remain after cleanup")
        return CheckResult("passed", "baseline directory and worktree registration removed")

    @staticmethod
    def _compare_baseline(checks: Mapping[str, CheckResult]
                          ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        new_failures: list[str] = []
        base_failures: list[str] = []
        inherited_failures: list[str] = []
        for name in ("build", "typecheck", "test_full", "reachability", "mutation"):
            current = checks.get(name)
            baseline = checks.get(f"base_{name}")
            if baseline is not None and baseline.status == "failed":
                base_failures.append(name)
            if current is None or current.status != "failed":
                continue
            if baseline is None or baseline.status != "failed":
                new_failures.append(name)
            elif (current.summary == baseline.summary and current.output_sha256
                  and current.output_sha256 == baseline.output_sha256):
                inherited_failures.append(name)
            else:
                new_failures.append(name)
        return tuple(new_failures), tuple(base_failures), tuple(inherited_failures)

    @staticmethod
    def _baseline_summary(new_failures: tuple[str, ...], base_failures: tuple[str, ...],
                          inherited_failures: tuple[str, ...],
                          owns_failures: tuple[str, ...], open_task_ids: tuple[str, ...],
                          baseline_unavailable: bool) -> str:
        parts: list[str] = []
        if baseline_unavailable:
            parts.append("baseline could not be verified or cleaned up")
        if new_failures:
            parts.append("new failures: " + ", ".join(new_failures))
        if inherited_failures:
            owners = sorted(set(owns_failures) & set(open_task_ids))
            if owners:
                parts.append("inherited failures " + ", ".join(inherited_failures)
                             + " owned by open tasks: " + ", ".join(owners))
            else:
                parts.append("unowned baseline failures: " + ", ".join(inherited_failures))
        fixed = sorted(set(base_failures) - set(inherited_failures))
        if fixed:
            parts.append("baseline failures fixed by this task: " + ", ".join(fixed))
        return "; ".join(parts) if parts else "no unowned baseline failures or newly introduced failures"

    def _run_command(self, name: str, argv: tuple[str, ...], worktree: Path,
                     env: dict[str, str], timeout_seconds: float) -> CheckResult:
        if not argv or not all(isinstance(arg, str) and arg for arg in argv):
            return CheckResult("failed", "configured command must be a non-empty argv list")
        started = time.monotonic()
        log_path = self.artifact_root / f"{name}-{time.time_ns()}.log"
        try:
            process = subprocess.run(list(argv), cwd=worktree, env=env,
                                     capture_output=True, text=True, encoding="utf-8",
                                     errors="replace", timeout=timeout_seconds)
            duration = time.monotonic() - started
            output = (process.stdout or "") + (process.stderr or "")
            safe_output = self._normalize_output(self._safe_log(output, env, argv), worktree)
            log_path.write_text(safe_output, encoding="utf-8")
            output_sha = hashlib.sha256(safe_output.encode("utf-8")).hexdigest()
            summary = f"exit code {process.returncode}"
            return CheckResult("passed" if process.returncode == 0 else "failed",
                               summary, duration, str(log_path), output_sha)
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - started
            output = (exc.stdout or b"") + (exc.stderr or b"")
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            safe_output = self._normalize_output(self._safe_log(str(output), env, argv), worktree)
            log_path.write_text(safe_output, encoding="utf-8")
            return CheckResult("failed", f"timed out after {timeout_seconds:g} seconds",
                               duration, str(log_path),
                               hashlib.sha256(safe_output.encode("utf-8")).hexdigest())
        except OSError as exc:
            return CheckResult("failed", f"could not start command: {exc}",
                               time.monotonic() - started, None)

    @staticmethod
    def _git(worktree: Path, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(worktree), *args], capture_output=True,
                                text=True, encoding="utf-8", errors="replace")
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
        return result.stdout

    @staticmethod
    def _git_common_dir(worktree: Path) -> Path:
        result = subprocess.run(["git", "-C", str(worktree), "rev-parse", "--git-common-dir"],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace")
        if result.returncode:
            raise RuntimeError("could not resolve Git common directory")
        value = Path(result.stdout.strip())
        return (worktree / value).resolve() if not value.is_absolute() else value.resolve()

    @staticmethod
    def _safe_log(output: str, env: dict[str, str],
                  command: tuple[str, ...] = ()) -> str:
        """Redact known environment/argv values and omit credential-like output."""
        if find_secrets(output):
            return "[redacted: secret-like output omitted]\n"
        redactable = {value for name, value in env.items()
                      if name != "CODEXDEVTEAM_RUN_SCOPE"}
        redactable.update(command[1:])
        for value in sorted(redactable, key=len, reverse=True):
            if len(value) >= 6:
                output = output.replace(value, "[REDACTED_VALUE]")
        return output

    @staticmethod
    def _normalize_output(output: str, worktree: Path) -> str:
        return output.replace(str(worktree), "<WORKTREE>").replace(
            worktree.as_posix(), "<WORKTREE>")

    @staticmethod
    def _skip_commands(checks: dict[str, CheckResult], commands: Mapping, reason: str) -> None:
        for name in ("build", "typecheck", "test_full", "reachability", "mutation"):
            if name not in checks:
                checks[name] = CheckResult("skipped", reason if commands.get(name) else "no command configured")

    @staticmethod
    def _fingerprint(task: TaskRecord, sha: str, base_ref: str | None, commands: Mapping,
                     allowed: tuple[str, ...], env: dict[str, str], timeout: float,
                     open_task_ids: tuple[str, ...],
                     per_worktree_environment: Mapping[str, str],
                     protected_paths: tuple[str, ...],
                     active_tasks: tuple[TaskRecord, ...]) -> str:
        env_hashes = {key: hashlib.sha256(env[key].encode("utf-8")).hexdigest()
                      for key in sorted(env)}
        owner_snapshot = [{
            "task_id": item.task_id,
            "state": item.state.value,
            "assigned_worker": item.assigned_worker,
            "owned_paths": item.owned_paths,
        } for item in sorted(active_tasks, key=lambda record: record.task_id)
            if item.state in {TaskState.CLAIMED, TaskState.IN_PROGRESS,
                              TaskState.NEEDS_REVIEW} and item.assigned_worker]
        body = {"task_id": task.task_id, "sha": sha, "base_ref": base_ref,
                "owned_paths": task.owned_paths,
                "protected_grants": task.protected_grants,
                "active_territory_owners": owner_snapshot,
                "owns_failures": task.owns_failures,
                "open_task_ids": sorted(open_task_ids),
                "protected_paths": sorted(protected_paths),
                "commands": {key: list(commands.get(key) or ()) for key in
                             ("build", "typecheck", "test_full", "reachability", "mutation")},
                "allowed_environment": sorted(allowed), "env_fingerprints": env_hashes,
                "per_worktree_environment": {
                    key: hashlib.sha256(value.encode("utf-8")).hexdigest()
                    for key, value in sorted(per_worktree_environment.items())
                },
                "timeout_seconds": timeout}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _failure_without_cache(self, task: TaskRecord, sha: str,
                               checks: dict[str, CheckResult], commands: Mapping) -> GateResult:
        self._skip_commands(checks, commands, "preflight verification failed")
        fingerprint = hashlib.sha256(f"{task.task_id}:{sha}:preflight-failed".encode()).hexdigest()
        return GateResult(task.task_id, sha, "failed", checks, fingerprint, "", False)

    @staticmethod
    def _save_result(path: Path, result: GateResult) -> None:
        value = {"task_id": result.task_id, "sha": result.sha, "status": result.status,
                 "fingerprint": result.fingerprint,
                 "checks": {name: {"status": check.status, "summary": check.summary,
                                   "duration_seconds": check.duration_seconds,
                                   "log_path": check.log_path,
                                   "output_sha256": check.output_sha256}
                            for name, check in result.checks.items()},
                 "new_failures": list(result.new_failures),
                 "base_failures": list(result.base_failures),
                 "inherited_failures": list(result.inherited_failures),
                 "failure_owner_ids": list(result.failure_owner_ids),
                 "open_failure_owner_ids": list(result.open_failure_owner_ids)}
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        temp.replace(path)

    @staticmethod
    def _load_cached(path: Path, task_id: str, sha: str, fingerprint: str) -> GateResult | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if (value.get("task_id") != task_id or value.get("sha") != sha
                    or value.get("fingerprint") != fingerprint
                    or value.get("status") not in {"passed", "failed"}):
                return None
            checks = {name: CheckResult(item["status"], item["summary"],
                                        item.get("duration_seconds", 0.0), item.get("log_path"),
                                        item.get("output_sha256"))
                      for name, item in value["checks"].items()}
            for check in checks.values():
                if check.status not in {"passed", "failed", "skipped"}:
                    return None
                if check.log_path and not Path(check.log_path).is_file():
                    return None
            return GateResult(task_id, sha, value["status"], checks, fingerprint, str(path), True,
                              tuple(value.get("new_failures", ())),
                              tuple(value.get("base_failures", ())),
                              tuple(value.get("inherited_failures", ())),
                              tuple(value.get("failure_owner_ids", ())),
                              tuple(value.get("open_failure_owner_ids", ())))
        except (OSError, ValueError, KeyError, TypeError):
            return None
