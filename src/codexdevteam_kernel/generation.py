"""Optional Wave O generation lane for fully specified new-file tasks.

The stream parser and verifier come from DEVDEPARTMENT Wave O. This host
adapter keeps Codex read-only, stages complete output as uncommitted task
worktree content, and leaves the normal maker, host commit, gate, and checker
path responsible for accepting it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .generation_stream import MARKER, PathPolicy, RunState, materialize, new_nonce, parse_segments
from .generation_verify import verify
from .host_config import WindowsHostConfig
from .host_commit import validate_owned_retry_worktree
from .host_runtime import HostRuntimeBindings, load_host_runtime, load_runtime_capacity
from .identity import WorkerIdentity
from .onboarding import OnboardingMode, inspect_project
from .protocol import TaskRecord
from .runtime import CodexExecAdapter, InvocationRequest, InvocationResult
from .state import HeadLease, StateStore
from .tasks import TaskState
from .worktrees import GitWorktreeManager


_DISCOVERY = re.compile(
    r"\b(investigat|debug|reproduc|root cause|profil|flaky|diagnos)", re.IGNORECASE
)
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class GenerationFile:
    path: str
    purpose: str
    exports: tuple[str, ...]
    est_lines: int


@dataclass(frozen=True)
class GenerationManifest:
    files: tuple[GenerationFile, ...]
    context: tuple[str, ...]
    conventions: str
    dependencies: dict[str, tuple[str, ...]]
    tests: str

    @classmethod
    def load(cls, path: Path) -> "GenerationManifest":
        if path.is_symlink() or not path.is_file():
            raise ValueError("generation manifest must be a regular file")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) != {
            "files", "context", "conventions", "dependencies", "tests"
        }:
            raise ValueError("generation manifest fields are invalid")
        raw_files = data["files"]
        if not isinstance(raw_files, list) or not raw_files:
            raise ValueError("generation manifest needs files")
        files: list[GenerationFile] = []
        for item in raw_files:
            if not isinstance(item, dict) or set(item) != {
                "path", "purpose", "exports", "est_lines"
            }:
                raise ValueError("generation file fields are invalid")
            path_value, purpose = item["path"], item["purpose"]
            exports, lines = item["exports"], item["est_lines"]
            if (not isinstance(path_value, str) or not isinstance(purpose, str)
                    or not purpose.strip() or not isinstance(exports, list)
                    or not all(isinstance(name, str) and _NAME.fullmatch(name)
                               for name in exports)
                    or not isinstance(lines, int) or isinstance(lines, bool)
                    or lines < 1):
                raise ValueError("generation file definition is invalid")
            files.append(GenerationFile(path_value, purpose, tuple(exports), lines))
        if len({item.path.casefold() for item in files}) != len(files):
            raise ValueError("generation manifest contains duplicate or case-aliased paths")
        context = data["context"]
        dependencies = data["dependencies"]
        if (not isinstance(context, list) or not context
                or not all(isinstance(item, str) for item in context)
                or not isinstance(dependencies, dict)
                or not all(isinstance(name, str) and isinstance(pins, list)
                           and all(isinstance(pin, str) and pin.strip() for pin in pins)
                           for name, pins in dependencies.items())
                or not isinstance(data["conventions"], str)
                or not isinstance(data["tests"], str) or not data["tests"].strip()):
            raise ValueError("generation context, dependencies, or tests are invalid")
        return cls(tuple(files), tuple(context), data["conventions"],
                   {name: tuple(pins) for name, pins in dependencies.items()},
                   data["tests"])

    @property
    def paths(self) -> list[str]:
        return [item.path for item in self.files]


@dataclass(frozen=True)
class GenerationConfig:
    max_continuations: int
    practical_ceiling_tokens: int
    tokens_per_line: int
    input_max_chars: int
    output_limit_chars: int
    timeout_seconds: int

    @classmethod
    def load(cls, root: Path) -> "GenerationConfig":
        path = root / ".codexdevteam" / "framework" / "generation.json"
        if path.is_symlink() or not path.is_file():
            raise ValueError("generation config is missing; upgrade the inactive framework")
        data = json.loads(path.read_text(encoding="utf-8"))
        required = {"protocol_version", "auto_enabled", "max_continuations",
                    "practical_ceiling_tokens", "tokens_per_line", "input_max_chars",
                    "output_limit_chars", "timeout_seconds"}
        if not isinstance(data, dict) or set(data) != required or data["protocol_version"] != 1:
            raise ValueError("generation config schema is invalid")
        if data["auto_enabled"] is not False:
            raise ValueError("automatic generation routing is not implemented")
        for key in required - {"protocol_version", "auto_enabled"}:
            value = data[key]
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"generation config {key} must be a positive integer")
        if data["max_continuations"] > 5:
            raise ValueError("generation continuations must be bounded at five")
        return cls(*(data[key] for key in (
            "max_continuations", "practical_ceiling_tokens", "tokens_per_line",
            "input_max_chars", "output_limit_chars", "timeout_seconds"
        )))


@dataclass(frozen=True)
class GenerationContext:
    config: WindowsHostConfig
    policy: GenerationConfig
    task: TaskRecord
    manifest: GenerationManifest
    manifest_sha256: str
    protected_paths: tuple[str, ...]
    worker_id: str
    worker_identity: WorkerIdentity
    reasoning_effort: str | None
    nonce: str
    packet: str
    packet_sha256: str
    blockers: tuple[str, ...]
    signals: tuple[str, ...]


def _safe_context(root: Path, relative: str) -> str:
    if PathPolicy(manifest=[relative]).violations(relative):
        raise ValueError(f"unsafe generation context path: {relative}")
    current = root
    for part in relative.split("/"):
        current /= part
        if current.is_symlink():
            raise ValueError(f"generation context traverses a symlink: {relative}")
    target = current.resolve(strict=True)
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError(f"generation context is outside project or not a file: {relative}")
    if target.stat().st_size > 200_000:
        raise ValueError(f"generation context is too large: {relative}")
    return target.read_text(encoding="utf-8")


def prepare_generation(project: str | Path, task_id: str, manifest_path: str | Path,
                       unit_id: str | None = None,
                       nonce: str | None = None) -> GenerationContext:
    root = Path(project).resolve(strict=True)
    inspection = inspect_project(root)
    if (inspection.mode is not OnboardingMode.CODEXDEVTEAM_UPGRADE
            or inspection.devdepartment_present):
        raise ValueError("generation requires a fresh CODEXDEVTEAM project")
    config = WindowsHostConfig.load(root)
    policy_config = GenerationConfig.load(root)
    store = StateStore(config.state_db)
    task = store.get_task(task_id)
    if task is None:
        raise ValueError(f"unknown task: {task_id}")
    manifest_input = Path(manifest_path)
    if manifest_input.is_symlink():
        raise ValueError("generation manifest cannot be a symlink")
    manifest_file = manifest_input.resolve(strict=True)
    if not manifest_file.is_relative_to(root):
        raise ValueError("generation manifest must be inside the project")
    manifest_relative = manifest_file.relative_to(root).as_posix()
    if PathPolicy(manifest=[manifest_relative]).violations(manifest_relative):
        raise ValueError("generation manifest must be project-owned specification content")
    manifest = GenerationManifest.load(manifest_file)
    manifest_sha = hashlib.sha256(manifest_file.read_bytes()).hexdigest()
    bindings = load_host_runtime(config)
    makers = [worker for worker in bindings.registry.active_workers()
              if worker.identity.role == config.maker_role]
    chosen = [worker for worker in makers if worker.identity.unit_id == unit_id] if unit_id else makers
    if len(chosen) != 1:
        raise ValueError("select exactly one active strict maker with --unit")
    worker = chosen[0]
    if worker.control_mode != "strict" or worker.identity.runtime != "codex":
        raise ValueError("generation requires a live-verified strict Codex maker")
    role_policy = bindings.registry.policy_for_role(worker.identity.role)
    reasoning_effort = role_policy.reasoning_effort if role_policy else None
    checkers = [item for item in bindings.registry.active_workers()
                if item.identity.role == config.checker_role]
    if any((item.identity.runtime, item.identity.model) ==
           (worker.identity.runtime, worker.identity.model) for item in checkers):
        raise ValueError("generator model must differ from every active checker model")
    territory = PathPolicy(
        manifest=manifest.paths, owned=list(task.owned_paths),
        grants=list(task.protected_grants),
        protected=list(bindings.protected_paths),
    )
    blockers: list[str] = []
    signals: list[str] = []
    source_status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all",
         "--", ".", ":(exclude).codexdevteam/**", ":(exclude)PLAN.md"],
        capture_output=True, text=True, check=False,
    )
    if source_status.returncode or source_status.stdout.strip():
        blockers.append("project source must be clean before generation staging")
    source_inputs = (manifest_relative, *manifest.context)
    for relative in source_inputs:
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative],
            capture_output=True, check=False,
        )
        if tracked.returncode:
            blockers.append(f"generation input is not tracked in the base commit: {relative}")
    if task.state is not TaskState.PENDING or task.assigned_worker is not None:
        blockers.append("only a pending, unassigned task can enter the manual generation lane")
    if not task.acceptance_criteria:
        blockers.append("task has no acceptance criteria")
    if _DISCOVERY.search(task.title):
        blockers.append("task title describes discovery work")
    for item in manifest.files:
        blockers.extend(territory.violations(item.path))
        if (root / item.path).exists():
            blockers.append(f"{item.path} already exists; generation creates new files only")
    branch = f"refs/heads/codexdevteam/{task.task_id}"
    if subprocess.run(["git", "-C", str(root), "show-ref", "--verify", "--quiet",
                       branch], check=False).returncode == 0:
        blockers.append("task branch already exists")
    if (config.worktree_root / task.task_id).exists():
        blockers.append("task worktree already exists")
    estimate = sum(item.est_lines for item in manifest.files) * policy_config.tokens_per_line
    segments = math.ceil(estimate / policy_config.practical_ceiling_tokens)
    if segments > 1 + policy_config.max_continuations:
        blockers.append("estimated output exceeds the bounded continuation budget")
    else:
        signals.append(f"estimated {estimate} output tokens across {segments} segment(s)")
    context_parts: list[str] = []
    for relative in manifest.context:
        if relative in manifest.paths:
            blockers.append(f"context file is also an output file: {relative}")
            continue
        try:
            context_parts.append(f"## Read-only context: {relative}\n{_safe_context(root, relative)}")
        except (OSError, UnicodeError, ValueError) as exc:
            blockers.append(str(exc))
    nonce = nonce or new_nonce()
    contract = (
        f"Generate complete files only. For each manifest path, emit a line "
        f"{MARKER} {nonce} FILE <path>, then all file content, then "
        f"{MARKER} {nonce} END <path>. Finish with {MARKER} {nonce} DONE. "
        "Use the exact paths and order below. Do not use tools, markdown fences, "
        "placeholders, stubs, or summaries. If output space runs short, finish "
        "the current file and stop; the host will ask for remaining files."
    )
    rows = [f"- {item.path}: {item.purpose}; exports: "
            + (", ".join(item.exports) or "none")
            for item in manifest.files]
    packet = "\n\n".join([
        contract,
        f"Task {task.task_id}: {task.title}",
        "Acceptance criteria:\n" + "\n".join(f"- {item}" for item in task.acceptance_criteria),
        "Conventions:\n" + manifest.conventions,
        "Pinned dependencies:\n" + json.dumps(manifest.dependencies, sort_keys=True),
        "Manifest in output order:\n" + "\n".join(rows),
        "Verification command: " + manifest.tests,
        *context_parts,
        f"Begin with {MARKER} {nonce} FILE {manifest.files[0].path}.",
    ])
    if len(packet) > policy_config.input_max_chars:
        blockers.append("generation packet exceeds configured input limit")
    return GenerationContext(config, policy_config, task, manifest, manifest_sha,
                             bindings.protected_paths, worker.identity.unit_id,
                             worker.identity, reasoning_effort, nonce, packet,
                             hashlib.sha256(packet.encode("utf-8")).hexdigest(),
                             tuple(blockers), tuple(signals))


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip()
                           or "Git command failed")
    return result.stdout.strip()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _run_root(context: GenerationContext) -> Path:
    return context.config.project_root / ".codexdevteam" / "state" / "generation" / context.task.task_id


def _segment_texts(run_dir: Path, state: RunState) -> list[str]:
    texts: list[str] = []
    for name in state.segments:
        if not re.fullmatch(r"segment-[0-9]+\.raw", name):
            raise ValueError("generation state contains an unsafe segment name")
        path = run_dir / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("generation segment is missing or a symlink")
        texts.append(path.read_text(encoding="utf-8"))
    return texts


def _continuation(context: GenerationContext, state: RunState, staging: Path) -> str:
    complete = [path for path in context.manifest.paths if path in state.written_hashes]
    remaining = [path for path in context.manifest.paths if path not in state.written_hashes]
    contents = []
    for relative in complete:
        path = staging.joinpath(*relative.split("/"))
        contents.append(f"Completed file {relative}:\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join([
        context.packet,
        "Continue this generation in a fresh bounded invocation. "
        "Do not repeat completed files. Emit remaining files from their first line.",
        "Completed paths: " + ", ".join(complete),
        "Remaining paths: " + ", ".join(remaining),
        *contents,
        f"Finish with {MARKER} {context.nonce} DONE.",
    ])


class _LeaseKeeper:
    def __init__(self, store: StateStore, lease: HeadLease, ttl_seconds: int):
        self.store, self.current, self.ttl_seconds = store, lease, ttl_seconds
        self.stop = threading.Event()
        self.cancel = threading.Event()
        self.error: Exception | None = None
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def __enter__(self) -> "_LeaseKeeper":
        self.thread.start()
        return self

    def _loop(self) -> None:
        while not self.stop.wait(max(1, self.ttl_seconds / 3)):
            try:
                self.current = self.store.renew_head(
                    self.current, ttl_seconds=self.ttl_seconds)
            except Exception as exc:
                self.error = exc
                self.cancel.set()
                return

    def verify(self) -> HeadLease:
        if self.error is not None or self.cancel.is_set():
            raise RuntimeError("generation lost its exclusive HEAD lease") from self.error
        self.current = self.store.renew_head(
            self.current, ttl_seconds=self.ttl_seconds)
        return self.current

    def __exit__(self, *_exc: object) -> None:
        self.stop.set()
        self.thread.join(timeout=5)
        self.store.release_head(self.current)


def _invoke_codex(context: GenerationContext, prompt: str,
                  cancel: threading.Event) -> InvocationResult:
    with tempfile.TemporaryDirectory(prefix="codexdevteam-generate-cwd-") as directory:
        cwd = Path(directory)
        subprocess.run(["git", "init", "--quiet", str(cwd)], check=True,
                       capture_output=True)
        request = InvocationRequest(
            invocation_id="generation-" + uuid.uuid4().hex,
            task_id=context.task.task_id,
            purpose="head",
            identity=context.worker_identity,
            prompt=prompt,
            working_directory=str(cwd),
            timeout_seconds=context.policy.timeout_seconds,
            writable=False,
            output_limit_chars=context.policy.output_limit_chars,
            cancel_event=cancel,
            reasoning_effort=context.reasoning_effort,
        )
        return CodexExecAdapter().invoke(request)


def _stage_files(context: GenerationContext, parsed, lease: HeadLease,
                 store: StateStore, run_dir: Path, base_sha: str) -> dict:
    root = context.config.project_root
    branch = f"codexdevteam/{context.task.task_id}"
    if subprocess.run(["git", "-C", str(root), "show-ref", "--verify", "--quiet",
                       f"refs/heads/{branch}"], check=False).returncode == 0:
        raise ValueError("task branch already exists; generation will not overwrite it")
    worktrees = GitWorktreeManager(root, context.config.worktree_root)
    worktree_path = worktrees.managed_root / context.task.task_id
    if worktree_path.exists():
        raise ValueError("task worktree already exists; generation will not reuse it")
    info = worktrees.create(context.task.task_id, branch, base_sha)
    policy = PathPolicy(context.manifest.paths, list(context.task.owned_paths),
                        list(context.task.protected_grants),
                        list(context.protected_paths))
    try:
        result = materialize(parsed, policy, info.path)
        if not result.complete or result.extraneous:
            raise ValueError("generation could not stage every file within task territory")
        finding = verify(info.path, list(context.manifest.files))
        hashes = {path: hashlib.sha256(
            info.path.joinpath(*path.split("/")).read_bytes()).hexdigest()
            for path in context.manifest.paths}
        receipt = {
            "protocol_version": 1,
            "task_id": context.task.task_id,
            "base_sha": base_sha,
            "branch": branch,
            "worktree": str(info.path),
            "manifest_sha256": context.manifest_sha256,
            "packet_sha256": context.packet_sha256,
            "generator": {"unit_id": context.worker_identity.unit_id,
                          "runtime": context.worker_identity.runtime,
                          "model": context.worker_identity.model,
                          "reasoning_effort": context.reasoning_effort},
            "files": hashes,
            "verify_ok": finding.ok,
            "verify_findings": [item.__dict__ for item in finding.findings],
            "staged_at": time.time(),
        }
        store.record_event(lease, "generation-staged-" + uuid.uuid4().hex,
                           {"type": "generation.staged", "task_id": context.task.task_id,
                            "branch": branch, "base_sha": base_sha,
                            "files": hashes, "verify_ok": finding.ok})
        _write_json(run_dir / "ready.json", receipt)
        return receipt
    except Exception:
        for relative in context.manifest.paths:
            target = info.path.joinpath(*relative.split("/"))
            if target.is_file() and not target.is_symlink():
                target.unlink()
        try:
            worktrees.remove(info.path)
        except Exception:
            pass
        raise


def _prepare_run_state(context: GenerationContext, run_dir: Path,
                       base_sha: str) -> tuple[RunState, Path, PathPolicy]:
    run_dir.mkdir(parents=True, exist_ok=True)
    contract_path = run_dir / "contract.json"
    expected = {
        "task_id": context.task.task_id, "nonce": context.nonce,
        "base_sha": base_sha, "manifest_sha256": context.manifest_sha256,
        "packet_sha256": context.packet_sha256, "unit_id": context.worker_id,
        "runtime": context.worker_identity.runtime,
        "model": context.worker_identity.model,
        "reasoning_effort": context.reasoning_effort,
    }
    if contract_path.exists():
        if contract_path.is_symlink() or not contract_path.is_file():
            raise ValueError("generation contract is not a regular file")
        if json.loads(contract_path.read_text(encoding="utf-8")) != expected:
            raise ValueError("generation inputs changed; refusing to resume")
    else:
        _write_json(contract_path, expected)
    if (run_dir / "ready.json").exists():
        raise ValueError("generation already staged; inspect the existing task worktree")
    staging = run_dir / "files"
    if staging.is_symlink():
        raise ValueError("generation staging directory is a symlink")
    staging.mkdir(exist_ok=True)
    state_path = run_dir / "state.json"
    if state_path.is_symlink():
        raise ValueError("generation state is a symlink")
    state = (RunState.load(state_path) if state_path.exists() else
             RunState(context.task.task_id, context.nonce, target_root=str(staging)))
    if state.nonce != context.nonce or state.target_root != str(staging):
        raise ValueError("generation state does not match its contract")
    policy = PathPolicy(context.manifest.paths, list(context.task.owned_paths),
                        list(context.task.protected_grants),
                        list(context.protected_paths))
    return state, staging, policy


def stage_generation(context: GenerationContext, *,
                     takeover_confirmed: bool = False,
                     invoke: Callable[[GenerationContext, str, threading.Event],
                                      InvocationResult] = _invoke_codex) -> dict:
    if context.blockers:
        raise ValueError("task is not eligible: " + "; ".join(context.blockers))
    config = context.config
    root = config.project_root
    store = StateStore(config.state_db)
    if store.get_supervisor_mode().get("mode") != "parked":
        raise ValueError("manual generation requires the supervisor to be parked")
    if config.instance_id == "configure-host-instance":
        raise ValueError("configure the host instance ID before generation")
    bindings = load_host_runtime(config)
    capacity = load_runtime_capacity(config, bindings)
    observation = capacity.get(context.worker_id)
    if observation is None or not observation.eligible(now=time.time()):
        raise ValueError("generator capacity is unavailable or stale")
    run_dir = _run_root(context)
    if any(path.is_symlink() for path in (root / ".codexdevteam",
                                          root / ".codexdevteam" / "state",
                                          run_dir.parent, run_dir)):
        raise ValueError("generation state path traverses a symlink")
    base_sha = _git(root, "rev-parse", "HEAD")
    lease = store.acquire_head(
        config.system_id, config.instance_id + "-generation",
        ttl_seconds=config.lease_ttl_seconds,
        takeover_confirmed=takeover_confirmed,
    )
    with _LeaseKeeper(store, lease, config.lease_ttl_seconds) as keeper:
        state, staging, policy = _prepare_run_state(context, run_dir, base_sha)
        state_path = run_dir / "state.json"
        parsed = parse_segments(context.nonce, _segment_texts(run_dir, state))
        materialized = materialize(parsed, policy, staging,
                                   owned_hashes=state.written_hashes)
        state.written_hashes = materialized.hashes
        state.pending = materialized.pending
        state.resume_from = materialized.resume_from
        state.complete = materialized.complete
        state.save(state_path)
        segments_this_run = 0
        max_segments = 1 + context.policy.max_continuations
        max_attempts = max_segments + 2
        prior_attempts = len(list(run_dir.glob("attempt-*.json")))
        while (not materialized.complete and len(state.segments) < max_segments
               and prior_attempts + segments_this_run < max_attempts):
            if materialized.errors or materialized.rejected or materialized.extraneous:
                break
            index = len(state.segments)
            prompt = context.packet if index == 0 else _continuation(context, state, staging)
            if len(prompt) > context.policy.input_max_chars:
                raise ValueError("continuation packet exceeds configured input limit")
            result = invoke(context, prompt, keeper.cancel)
            keeper.verify()
            valid_result = (
                result.status == "succeeded" and not result.truncated
                and result.task_id == context.task.task_id
                and result.purpose == "head"
                and result.role == context.worker_identity.role
                and result.unit_id == context.worker_id
                and result.runtime == context.worker_identity.runtime
                and result.model == context.worker_identity.model
            )
            if valid_result:
                (run_dir / f"segment-{index}.raw").write_text(
                    result.stdout, encoding="utf-8", newline="\n")
            receipt = {
                "invocation_id": result.invocation_id, "task_id": result.task_id,
                "purpose": result.purpose, "role": result.role,
                "unit_id": result.unit_id, "runtime": result.runtime, "model": result.model,
                "reasoning_effort": context.reasoning_effort,
                "status": result.status, "exit_code": result.exit_code,
                "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                "cached_input_tokens": result.cached_input_tokens,
                "output_sha256": result.output_sha256,
                "raw_sha256": hashlib.sha256(result.stdout.encode("utf-8")).hexdigest()
                if valid_result else None,
                "truncated": result.truncated, "duration_seconds": result.duration_seconds,
            }
            attempt_id = uuid.uuid4().hex
            receipt["segment"] = index
            _write_json(run_dir / f"attempt-{attempt_id}.json", receipt)
            store.record_event(keeper.current, f"generation-attempt-{attempt_id}",
                               {"type": "generation.segment", **receipt})
            if not valid_result:
                break
            state.segments.append(f"segment-{index}.raw")
            state.save(state_path)
            before = len(state.written_hashes)
            parsed = parse_segments(context.nonce, _segment_texts(run_dir, state))
            materialized = materialize(parsed, policy, staging,
                                       owned_hashes=state.written_hashes)
            state.written_hashes = materialized.hashes
            state.pending = materialized.pending
            state.resume_from = materialized.resume_from
            state.complete = materialized.complete
            state.save(state_path)
            segments_this_run += 1
            if len(state.written_hashes) == before and not materialized.complete:
                break
        if not materialized.complete or materialized.extraneous:
            return {"status": "incomplete", "task_id": context.task.task_id,
                    "segments_this_run": segments_this_run,
                    "remaining_segment_budget": max(0, max_segments - len(state.segments)),
                    "remaining_attempt_budget": max(0, max_attempts - prior_attempts - segments_this_run),
                    "pending": materialized.pending, "rejected": materialized.rejected,
                    "errors": materialized.errors, "extraneous": materialized.extraneous}
        keeper.verify()
        return {"status": "staged", "task_id": context.task.task_id,
                "segments_this_run": segments_this_run,
                "receipt": _stage_files(context, parsed, keeper.current,
                                        store, run_dir, base_sha)}


def _validated_generation_files(config: WindowsHostConfig, task: TaskRecord,
                                bindings: HostRuntimeBindings) -> dict[str, str] | None:
    """Verify the receipt, Git worktree, exact changed paths, and file bytes."""
    path = (config.project_root / ".codexdevteam" / "state" / "generation" /
            task.task_id / "ready.json")
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("generation ready receipt is unsafe")
    receipt = json.loads(path.read_text(encoding="utf-8"))
    branch = f"codexdevteam/{task.task_id}"
    worktrees = GitWorktreeManager(config.project_root, config.worktree_root)
    worktree = worktrees.inspect(config.worktree_root / task.task_id)
    if (receipt.get("protocol_version") != 1
            or receipt.get("task_id") != task.task_id or receipt.get("branch") != branch
            or receipt.get("worktree") != str(worktree.path)
            or worktree.branch != branch):
        raise ValueError("generation ready receipt does not match the task worktree")
    base_sha = receipt.get("base_sha")
    if (not isinstance(base_sha, str) or not re.fullmatch(r"[0-9a-f]{40,64}", base_sha)
            or worktree.head != base_sha or _git(config.project_root, "rev-parse", "HEAD") != base_sha):
        raise ValueError("generation task worktree no longer matches its base commit")
    generator = receipt.get("generator")
    worker = (bindings.registry.defined.get(generator.get("unit_id"))
              if isinstance(generator, dict) else None)
    role_policy = (bindings.registry.policy_for_role(worker.identity.role)
                   if worker is not None else None)
    expected_generator = ({"unit_id": worker.identity.unit_id,
                           "runtime": worker.identity.runtime,
                           "model": worker.identity.model,
                           "reasoning_effort": role_policy.reasoning_effort if role_policy else None}
                          if worker is not None else None)
    if (worker is None or worker.identity.unit_id not in bindings.registry.active
            or worker.control_mode != "strict"
            or worker.identity.role != config.maker_role
            or expected_generator != generator):
        raise ValueError("generation receipt generator no longer matches an active strict maker")
    files = receipt.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("generation ready receipt has no file hashes")
    policy = PathPolicy(list(files), list(task.owned_paths),
                        list(task.protected_grants), list(bindings.protected_paths))
    for relative, expected in files.items():
        if (not isinstance(relative, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)
                or policy.violations(relative)):
            raise ValueError("generation ready receipt contains an unsafe path")
        target = worktree.path.joinpath(*relative.split("/"))
        current = worktree.path
        for part in relative.split("/"):
            current /= part
            if current.is_symlink():
                raise ValueError("generation staged path traverses a symlink")
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise ValueError(f"generation staged file changed before dispatch: {relative}")
    changed = validate_owned_retry_worktree(
        config.project_root, worktree.path, task, task_branch=branch,
        expected_parent=base_sha,
        ignored_allowlist=bindings.ignored_paths_allowlist,
    )
    if set(changed) != set(files):
        raise ValueError("generation worktree has changes outside the staged file receipt")
    return dict(files)


def generation_maker_note(config: WindowsHostConfig, task: TaskRecord,
                          bindings: HostRuntimeBindings) -> str:
    """Verify a ready receipt before a pending task can be dispatched."""
    if _validated_generation_files(config, task, bindings) is None:
        return ""
    return (
        "\n\nHOST-STAGED GENERATION\n"
        "The task worktree already contains generated new files. Inspect them as untrusted "
        "drafts, run the full project checks, repair within Owned_Paths, and report facts. "
        "The host will commit the final bytes only after your process tree exits; "
        "the normal gate and independent checker still decide acceptance."
    )


def generation_prelaunch_ready(config: WindowsHostConfig, task: TaskRecord,
                               bindings: HostRuntimeBindings, worktree: Path,
                               base_ref: str) -> dict[str, str] | None:
    """Recheck the exact host-staged draft immediately before maker launch."""
    if not (config.worktree_root / task.task_id).resolve() == worktree.resolve():
        raise ValueError("generation prelaunch worktree differs from the managed task worktree")
    if _git(config.project_root, "rev-parse", "HEAD") != base_ref:
        raise ValueError("generation prelaunch base differs from the project HEAD")
    return _validated_generation_files(config, task, bindings)


def _existing_nonce(project: Path, task_id: str) -> str | None:
    path = (project / ".codexdevteam" / "state" / "generation" /
            task_id / "contract.json")
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("generation contract is not a regular file")
    data = json.loads(path.read_text(encoding="utf-8"))
    nonce = data.get("nonce")
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{12}", nonce):
        raise ValueError("generation contract nonce is invalid")
    return nonce


def generation_report(project: Path, task_id: str) -> dict:
    run_dir = (project / ".codexdevteam" / "state" / "generation" / task_id)
    if not run_dir.is_dir() or run_dir.is_symlink():
        raise ValueError("generation has no recorded run")
    receipts = []
    for path in sorted(run_dir.glob("attempt-*.json")):
        if not re.fullmatch(r"attempt-[0-9a-f]{32}\.json", path.name) or path.is_symlink():
            raise ValueError("generation segment receipt is unsafe")
        receipts.append(json.loads(path.read_text(encoding="utf-8")))
    ready = run_dir / "ready.json"
    totals = {}
    for key in ("input_tokens", "output_tokens", "cached_input_tokens"):
        values = [item.get(key) for item in receipts]
        totals[key] = sum(values) if values and all(
            isinstance(value, int) and value >= 0 for value in values) else None
    return {
        "task_id": task_id,
        "segments": len(receipts),
        "staged": ready.is_file() and not ready.is_symlink(),
        "usage": totals,
        "duration_seconds": sum(item.get("duration_seconds", 0) for item in receipts),
        "statuses": [item.get("status") for item in receipts],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="codexdevteam-generate",
        description="Manual, bounded generation lane for new-file tasks",
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--unit")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("classify")
    commands.add_parser("packet")
    stage = commands.add_parser("stage")
    stage.add_argument("--confirm-stage", action="store_true")
    stage.add_argument("--confirm-prior-lease-takeover", action="store_true")
    verify_command = commands.add_parser("verify")
    verify_command.add_argument("--target")
    commands.add_parser("report")
    args = parser.parse_args(argv)
    try:
        project = Path(args.project).resolve(strict=True)
        if args.command == "report":
            result = generation_report(project, args.task)
        else:
            context = prepare_generation(
                project, args.task, args.manifest, args.unit,
                nonce=_existing_nonce(project, args.task),
            )
            if args.command == "classify":
                result = {
                    "task_id": args.task,
                    "lane": "generate" if not context.blockers else "iterate",
                    "blockers": list(context.blockers),
                    "signals": list(context.signals),
                    "generator": {"unit_id": context.worker_identity.unit_id,
                                  "runtime": context.worker_identity.runtime,
                                  "model": context.worker_identity.model,
                                  "reasoning_effort": context.reasoning_effort},
                }
            elif args.command == "packet":
                if context.blockers:
                    raise ValueError("task is not eligible: " + "; ".join(context.blockers))
                result = {"task_id": args.task, "nonce": context.nonce,
                          "packet_sha256": context.packet_sha256,
                          "packet": context.packet}
            elif args.command == "stage":
                if not args.confirm_stage:
                    raise ValueError("stage requires --confirm-stage")
                if os.name != "nt":
                    raise ValueError("Codex generation staging is supported on Windows only")
                result = stage_generation(
                    context,
                    takeover_confirmed=args.confirm_prior_lease_takeover,
                )
            else:
                target = (Path(args.target).resolve(strict=True)
                          if args.target else
                          context.config.worktree_root / args.task)
                finding = verify(target, list(context.manifest.files))
                result = finding.to_dict()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("status") != "incomplete" and result.get("ok") is not False else 1
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        parser.exit(1, f"CODEXDEVTEAM generation failed: {exc}\n")
