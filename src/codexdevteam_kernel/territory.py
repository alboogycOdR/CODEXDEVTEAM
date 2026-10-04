"""Repository-relative write territory checks."""

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import Iterable


@dataclass(frozen=True, slots=True)
class TerritoryDecision:
    allowed: bool
    path: str
    reason: str


def normalize_repo_path(path: str) -> str:
    """Normalize a repository path and reject absolute or escaping paths."""
    if not isinstance(path, str) or not path.strip() or "\\" in path:
        raise ValueError("path must be a non-empty repository-relative POSIX path")
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in path.split("/")):
        raise ValueError("path must not be absolute, empty, or traverse directories")
    return candidate.as_posix()


def _matches(path: str, pattern: str) -> bool:
    """Match a path with segment-aware `*` and recursive `**` semantics."""
    path_parts = path.split("/")
    pattern_parts = pattern.split("/")

    def visit(path_i: int, pattern_i: int) -> bool:
        if pattern_i == len(pattern_parts):
            return path_i == len(path_parts)
        part = pattern_parts[pattern_i]
        if part == "**":
            return visit(path_i, pattern_i + 1) or (
                path_i < len(path_parts) and visit(path_i + 1, pattern_i)
            )
        return (
            path_i < len(path_parts)
            and fnmatchcase(path_parts[path_i], part)
            and visit(path_i + 1, pattern_i + 1)
        )

    return visit(0, 0)


def decide_write(path: str, owned_paths: Iterable[str], *,
                 protected_paths: Iterable[str] = (),
                 actor_known: bool = True) -> TerritoryDecision:
    """Decide whether a write fits an actor's task territory.

    Protected paths win over ownership. Unknown actors fail closed. This is a
    policy primitive; runtime adapters still need to intercept writes and a
    post-run diff check remains necessary.
    """
    normalized = normalize_repo_path(path)
    if not actor_known:
        return TerritoryDecision(False, normalized, "unknown actor")
    protected = [normalize_repo_path(item) for item in protected_paths]
    if any(_matches(normalized, pattern) for pattern in protected):
        return TerritoryDecision(False, normalized, "protected path")
    owned = [normalize_repo_path(item) for item in owned_paths]
    if any(_matches(normalized, pattern) for pattern in owned):
        return TerritoryDecision(True, normalized, "within owned paths")
    return TerritoryDecision(False, normalized, "outside owned paths")


def validate_grant(path: str, *, allowed_patterns: Iterable[str],
                   other_active_owners: Iterable[str] = ()) -> TerritoryDecision:
    """Validate a narrowly configured grant request without mutating policy."""
    normalized = normalize_repo_path(path)
    allowed = [normalize_repo_path(item) for item in allowed_patterns]
    conflicts = [normalize_repo_path(item) for item in other_active_owners]
    if any(_matches(normalized, pattern) for pattern in conflicts):
        return TerritoryDecision(False, normalized, "owned by another active task")
    if any(_matches(normalized, pattern) for pattern in allowed):
        return TerritoryDecision(True, normalized, "grant matches an allowed pattern")
    return TerritoryDecision(False, normalized, "grant is outside allowed patterns")
