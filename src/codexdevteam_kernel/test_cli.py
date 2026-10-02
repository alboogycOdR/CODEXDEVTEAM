"""Builder-facing runner for configured, SHA-bound mechanical test evidence."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from .gate import GateRunner
from .protocol import TaskRecord
from .tasks import TaskState


def _load_config(path: Path, name: str) -> tuple[list[str], tuple[str, ...], dict[str, str], float]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("could not read test configuration") from exc
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("test configuration must use version 1")
    tests = config.get("tests")
    if not isinstance(tests, dict) or name not in tests or not isinstance(tests[name], dict):
        raise ValueError("requested test name is not configured")
    spec = tests[name]
    command = spec.get("argv")
    allowed = config.get("allowed_environment", [])
    templates = spec.get("per_worktree_environment", {})
    timeout = spec.get("timeout_seconds", 1800)
    if (not isinstance(command, list) or not command
            or not all(isinstance(item, str) and item for item in command)):
        raise ValueError("configured argv must be a non-empty string list")
    if not isinstance(allowed, list) or not all(
        isinstance(item, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item)
        for item in allowed
    ):
        raise ValueError("allowed_environment must be a list of environment names")
    if not isinstance(templates, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in templates.items()
    ):
        raise ValueError("per_worktree_environment must map names to templates")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("timeout_seconds must be positive")
    return command, tuple(allowed), templates, float(timeout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--worker", required=True)
    parser.add_argument("--worktree", type=Path, default=Path.cwd())
    parser.add_argument("--sha", required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", args.name):
        parser.error("--name must be a safe configured test name")
    if not re.fullmatch(r"TASK-[A-Z0-9][A-Z0-9-]*", args.task):
        parser.error("--task must be a valid task ID")
    if not args.worker.strip() or not re.fullmatch(r"[0-9a-fA-F]{40,64}", args.sha):
        parser.error("--worker must be non-empty and --sha must be a full Git SHA")
    try:
        command, allowed, templates, timeout = _load_config(args.config, args.name)
        worktree = args.worktree.resolve(strict=True)
        root_result = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False,
        )
        if root_result.returncode:
            raise ValueError("worktree is not inside a Git repository")
        root = Path(root_result.stdout.strip()).resolve()
        task = TaskRecord(args.task, "Configured test run", TaskState.IN_PROGRESS,
                          args.worker, "medium", ("**",))
        result = GateRunner(root).run_test_command(
            task, command, worktree=worktree, expected_sha=args.sha,
            allowed_environment=allowed, per_worktree_environment=templates,
            timeout_seconds=timeout, name=args.name,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({
        "status": "passed" if result.passed else "failed",
        "task_id": args.task,
        "worker_id": args.worker,
        "sha": result.sha,
        "test_name": result.name,
        "cached": result.cached,
        "exit_code": result.exit_code,
        "duration_seconds": result.duration_seconds,
        "output_sha256": result.output_sha256,
        "evidence_ref": result.evidence_ref,
        "artifact_path": result.artifact_path,
    }, sort_keys=True))
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
