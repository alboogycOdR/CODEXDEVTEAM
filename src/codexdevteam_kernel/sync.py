"""Conservative framework-file sync with explicit ownership and preconditions."""

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

from .onboarding import OnboardingMode


FRAMEWORK_PREFIX = ".codexdevteam/framework/"
PROJECT_STATE_PREFIX = ".codexdevteam/project/"


@dataclass(frozen=True, slots=True)
class SyncAction:
    path: str
    operation: str
    expected_sha256: str | None
    content: bytes


@dataclass(frozen=True, slots=True)
class SyncPlan:
    root: str
    mode: OnboardingMode
    actions: tuple[SyncAction, ...]
    conflicts: tuple[str, ...]


class SyncConflict(RuntimeError):
    """Raised when a planned sync would overwrite project or incumbent data."""


def _safe_relative(path: str) -> str:
    if not isinstance(path, str) or "\\" in path:
        raise ValueError("sync paths must use repository-relative POSIX form")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or not parsed.parts or any(part in {"", ".", ".."} for part in parsed.parts):
        raise ValueError("sync path must be relative and cannot traverse")
    return parsed.as_posix()


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def plan_framework_sync(root: str | Path, desired_files: Mapping[str, bytes | str], *,
                        prior_hashes: Mapping[str, str] | None = None,
                        mode: OnboardingMode) -> SyncPlan:
    """Plan creates/clean updates only under the CODEXDEVTEAM framework-owned prefix."""
    project = Path(root).resolve()
    if not project.is_dir():
        raise ValueError("project root must be an existing directory")
    prior_hashes = prior_hashes or {}
    if not isinstance(mode, OnboardingMode):
        raise ValueError("mode must be an OnboardingMode")
    actions: list[SyncAction] = []
    conflicts: list[str] = []
    for raw_path, value in desired_files.items():
        path = _safe_relative(raw_path)
        if not path.startswith(FRAMEWORK_PREFIX):
            conflicts.append(f"{path}: not a CODEXDEVTEAM framework-owned path")
            continue
        content = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        destination = project.joinpath(*PurePosixPath(path).parts)
        try:
            destination.resolve(strict=False).relative_to(project)
        except ValueError:
            conflicts.append(f"{path}: resolves outside the project root")
            continue
        if _has_symlink_parent(project, destination):
            conflicts.append(f"{path}: path contains a symlink")
            continue
        if not destination.exists():
            actions.append(SyncAction(path, "create", None, content))
            continue
        current = destination.read_bytes()
        current_hash = _digest(current)
        if current == content:
            continue
        previous_hash = prior_hashes.get(path)
        if previous_hash and current_hash == previous_hash:
            actions.append(SyncAction(path, "update", current_hash, content))
        else:
            conflicts.append(f"{path}: local file differs from the last managed version")
    return SyncPlan(str(project), mode, tuple(actions), tuple(conflicts))


def apply_framework_sync(plan: SyncPlan) -> tuple[str, ...]:
    """Apply a conflict-free plan after rechecking every planned file hash."""
    if plan.conflicts:
        raise SyncConflict("sync has unresolved ownership conflicts")
    root = Path(plan.root).resolve()
    staged: list[tuple[Path, Path, bytes | None]] = []
    committed: list[tuple[Path, bytes | None]] = []
    try:
        for action in plan.actions:
            try:
                path = _safe_relative(action.path)
            except ValueError as exc:
                raise SyncConflict(f"invalid sync path: {action.path}") from exc
            if not path.startswith(FRAMEWORK_PREFIX):
                raise SyncConflict(f"{path}: not a CODEXDEVTEAM framework-owned path")
            destination = root.joinpath(*PurePosixPath(path).parts)
            try:
                destination.resolve(strict=False).relative_to(root)
            except ValueError as exc:
                raise SyncConflict(f"{path}: resolves outside the project root") from exc
            if _has_symlink_parent(root, destination):
                raise SyncConflict(f"{action.path}: path became a symlink after planning")
            current = destination.read_bytes() if destination.exists() else None
            actual_hash = _digest(current) if current is not None else None
            if actual_hash != action.expected_sha256:
                raise SyncConflict(f"{action.path}: changed since planning")
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=".codexdevteam-sync-", dir=destination.parent)
            os.close(fd)
            temp_path = Path(temp_name)
            temp_path.write_bytes(action.content)
            staged.append((destination, temp_path, current))
        for destination, temp_path, previous in staged:
            if _has_symlink_parent(root, destination):
                raise SyncConflict(f"{destination}: path became a symlink before replacement")
            current = destination.read_bytes() if destination.exists() else None
            staged_expected = _digest(previous) if previous is not None else None
            if ( _digest(current) if current is not None else None) != staged_expected:
                raise SyncConflict(f"{destination}: changed before replacement")
            os.replace(temp_path, destination)
            committed.append((destination, previous))
    except Exception:
        for destination, previous in reversed(committed):
            if previous is None:
                destination.unlink(missing_ok=True)
            else:
                _atomic_restore(destination, previous)
        raise
    finally:
        for _, temp_path, _ in staged:
            temp_path.unlink(missing_ok=True)
    return tuple(action.path for action in plan.actions)


def three_way_merge_json(base: object, local: object, incoming: object,
                         path: str = "") -> tuple[object, tuple[str, ...]]:
    """Merge JSON trees; simultaneous divergent edits become named conflicts."""
    if local == incoming:
        return local, ()
    if local == base:
        return incoming, ()
    if incoming == base:
        return local, ()
    if all(isinstance(item, dict) for item in (base, local, incoming)):
        merged: dict = {}
        conflicts: list[str] = []
        keys = set(base) | set(local) | set(incoming)
        missing = object()
        for key in sorted(keys, key=str):
            value, issues = three_way_merge_json(base.get(key, missing),
                                                 local.get(key, missing),
                                                 incoming.get(key, missing),
                                                 f"{path}.{key}" if path else str(key))
            if value is not missing:
                merged[key] = value
            conflicts.extend(issues)
        return merged, tuple(conflicts)
    return local, (path or "<root>",)


def _has_symlink_parent(root: Path, destination: Path) -> bool:
    current = root
    try:
        relative = destination.relative_to(root)
    except ValueError:
        return True
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _atomic_restore(destination: Path, content: bytes) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=".codexdevteam-restore-", dir=destination.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        temp_path.write_bytes(content)
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)
