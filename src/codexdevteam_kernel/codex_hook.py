"""Codex PreToolUse adapter for explicit file-write tool events.

This is a preventative guard for structured edit tools, not a shell sandbox.
Post-run territory verification remains mandatory for strict workers.
"""

import json
import os
import re
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import quote

from .firewall import TerritoryPolicy
from .identity import WorkerIdentity
from .protocol import TaskRecord
from .secrets import find_secrets


_PATCH_PATH = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+?)\s*$", re.MULTILINE)
_MOVE_PATH = re.compile(r"^\*\*\* Move to: (.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class HookDecision:
    allowed: bool
    reason: str = ""

    def to_codex_response(self) -> dict:
        if self.allowed:
            return {}
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": self.reason or "CODEXDEVTEAM write policy denied this edit.",
        }}


def evaluate_codex_file_event(event: object, environment: Mapping[str, str]) -> HookDecision:
    """Evaluate a Codex edit event; unexpected input is explicitly denied."""
    try:
        if not isinstance(event, dict) or event.get("hook_event_name") != "PreToolUse":
            return HookDecision(False, "Invalid CODEXDEVTEAM PreToolUse event.")
        tool_name = event.get("tool_name")
        tool_input = event.get("tool_input")
        if not isinstance(tool_input, dict):
            return HookDecision(False, "Missing or invalid tool input; edit denied.")
        paths, content = _extract_edit(tool_name, tool_input)
        if not paths:
            return HookDecision(False, "Could not identify every edited path; edit denied.")
        if find_secrets(content):
            return HookDecision(False, "Credential-like content detected; edit denied.")

        actor_id = environment.get("CODEXDEVTEAM_WORKER_ID", "")
        task_id = environment.get("CODEXDEVTEAM_TASK_ID", "")
        database_value = environment.get("CODEXDEVTEAM_STATE_DB", "")
        repository = _resolve_repository(event.get("cwd"))
        if not actor_id or not task_id or not database_value:
            return HookDecision(False, "CODEXDEVTEAM task identity is unavailable; edit denied.")
        database = Path(database_value)
        tasks = _read_task_snapshot(database)
        actor = _identity_snapshot(environment, actor_id)
        policy = TerritoryPolicy(repository, protected_paths=("PLAN.md", ".codexdevteam/**"))
        authorization = policy.authorize(actor=actor, task_id=task_id,
                                         paths=paths, tasks=tasks)
        if not authorization.allowed:
            reasons = sorted({item.reason for item in authorization.decisions
                              if not item.allowed})
            return HookDecision(False, "Write policy denied edit: " + "; ".join(reasons))
        return HookDecision(True)
    except Exception:
        # Codex continues after a failed hook callback, so convert every adapter
        # failure into a valid explicit denial rather than relying on exit status.
        return HookDecision(False, "CODEXDEVTEAM write-policy check failed; edit denied.")


def _extract_edit(tool_name: object, tool_input: dict) -> tuple[tuple[str, ...], str]:
    if tool_name == "apply_patch":
        command = tool_input.get("command")
        if not isinstance(command, str) or not command.startswith("*** Begin Patch\n"):
            return (), ""
        if not command.rstrip().endswith("*** End Patch"):
            return (), command
        valid_markers = ("*** Begin Patch", "*** End Patch", "*** End of File")
        for line in command.splitlines():
            if not line.startswith("*** ") or line.startswith(valid_markers):
                continue
            if not re.match(r"^\*\*\* (?:Update|Add|Delete) File: .+|^\*\*\* Move to: .+$", line):
                return (), command
        paths = _PATCH_PATH.findall(command)
        paths.extend(_MOVE_PATH.findall(command))
        return tuple(dict.fromkeys(paths)), command
    if tool_name in {"Edit", "Write"}:
        path = tool_input.get("file_path", tool_input.get("path"))
        content = (tool_input.get("new_string") if tool_name == "Edit"
                   else tool_input.get("content"))
        if not isinstance(content, str):
            return (), ""
        return ((path,) if isinstance(path, str) and path else ()), content
    return (), ""


def _resolve_repository(cwd: object) -> Path:
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("hook cwd missing")
    import subprocess

    result = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                            capture_output=True, text=True, check=False, timeout=5)
    if result.returncode:
        raise ValueError("hook cwd is not a Git worktree")
    return Path(result.stdout.strip()).resolve(strict=True)


def _read_task_snapshot(database: Path) -> tuple[TaskRecord, ...]:
    resolved = database.resolve(strict=True)
    if database.is_symlink() or not resolved.is_file():
        raise ValueError("state database is not a regular file")
    uri = "file:" + quote(resolved.as_posix(), safe="/:\\") + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=2)) as connection:
        rows = connection.execute("SELECT payload_json FROM tasks ORDER BY task_id").fetchall()
    return tuple(TaskRecord.from_dict(json.loads(row[0])) for row in rows)


def _identity_snapshot(environment: Mapping[str, str], unit_id: str) -> WorkerIdentity:
    values = {
        "role": environment.get("CODEXDEVTEAM_WORKER_ROLE", ""),
        "capability_floor": environment.get("CODEXDEVTEAM_CAPABILITY_FLOOR", ""),
        "runtime": environment.get("CODEXDEVTEAM_RUNTIME", ""),
        "model": environment.get("CODEXDEVTEAM_MODEL", ""),
    }
    return WorkerIdentity(unit_id, **values)


def main() -> int:
    try:
        event = json.load(sys.stdin)
        decision = evaluate_codex_file_event(event, os.environ)
    except Exception:
        decision = HookDecision(False, "CODEXDEVTEAM write-policy input failed; edit denied.")
    response = decision.to_codex_response()
    if response:
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
