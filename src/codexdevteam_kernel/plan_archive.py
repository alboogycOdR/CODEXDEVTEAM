"""Deterministic Markdown adapter for completed-task archival.

The caller owns HEAD lease validation and PLAN compare-and-swap persistence.
This module plans archival and writes only append-only archive artifacts.
"""

from dataclasses import dataclass
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import os
import json
from pathlib import Path, PurePosixPath
import re
import tempfile


_TASK_HEADER = re.compile(r"^### (TASK-[A-Z0-9][A-Z0-9-]*)[ \t]*\r?$", re.MULTILINE)
_FIELD = re.compile(r"^\*\*([A-Za-z_]+):\*\*[ \t]*(.*)$")
_WAVE = re.compile(r"\bWave\s+([A-Z])\b", re.IGNORECASE)
_UPDATED = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T")


@dataclass(frozen=True, slots=True)
class ArchivedBlock:
    task_id: str
    month: str
    relative_path: str
    block: str


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    text: str
    blocks: tuple[ArchivedBlock, ...] = ()
    findings: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.blocks)


class ArchiveConflict(RuntimeError):
    """An archive destination changed or contains conflicting task data."""


@dataclass(frozen=True, slots=True)
class NotesRotation:
    text: str
    relative_path: str | None
    body: str | None
    original_chars: int


def iter_task_blocks(text: str) -> tuple[tuple[str, str], ...]:
    """Return task IDs and exact source slices without normalizing line endings."""
    headers = list(_TASK_HEADER.finditer(text))
    return tuple((match.group(1), text[match.start():
                                       headers[index + 1].start()
                                       if index + 1 < len(headers) else len(text)])
                 for index, match in enumerate(headers))


def plan_archive(text: str) -> ArchiveResult:
    """Replace done tasks from older waves with dependency-preserving stubs."""
    if not isinstance(text, str):
        raise ValueError("PLAN source must be text")
    blocks = iter_task_blocks(text)
    if not blocks:
        return ArchiveResult(text)
    ids = [task_id for task_id, _block in blocks]
    duplicates = sorted(task_id for task_id, count in Counter(ids).items() if count > 1)
    if duplicates:
        return ArchiveResult(text, findings=tuple(
            f"{task_id}: duplicate task ID prevents archival" for task_id in duplicates
        ))
    parsed = [(task_id, block, _fields(block)) for task_id, block in blocks]
    waves: list[str] = []
    open_waves: list[str] = []
    for _task_id, _block, values in parsed:
        if values.get("Archived", "").strip():
            continue
        match = _WAVE.search(values.get("Title", ""))
        if match:
            wave = match.group(1).upper()
            waves.append(wave)
            if values.get("Status", "").strip().lower() != "done":
                open_waves.append(wave)
    current_wave = max(open_waves or waves, default=None)
    if current_wave is None:
        return ArchiveResult(text)

    header = _TASK_HEADER.search(text)
    assert header is not None
    parts = [text[:header.start()]]
    archived: list[ArchivedBlock] = []
    findings: list[str] = []
    newline = "\r\n" if "\r\n" in text else "\n"
    for task_id, block, values in parsed:
        title = values.get("Title", "")
        wave_match = _WAVE.search(title)
        old_wave = wave_match.group(1).upper() if wave_match else None
        is_done = values.get("Status", "").strip().lower() == "done"
        eligible = (is_done and not values.get("Archived", "").strip()
                    and (old_wave is None or old_wave < current_wave))
        if not eligible:
            parts.append(block)
            continue
        month = _month_of(values.get("Updated_At", ""))
        if month is None:
            findings.append(f"{task_id}: missing or invalid Updated_At; left in PLAN")
            parts.append(block)
            continue
        relative_path = f"plan/archive/{month}.md"
        archived.append(ArchivedBlock(task_id, month, relative_path, block))
        parts.append(
            f"### {task_id}{newline}"
            f"**Status:** done{newline}"
            f"**Archived:** {relative_path}{newline}{newline}"
        )
    return ArchiveResult("".join(parts), tuple(archived), tuple(findings))


def append_archive_blocks(project_root: str | Path,
                          result: ArchiveResult) -> tuple[str, ...]:
    """Append exact task blocks to monthly archives, idempotently.

    PLAN persistence is intentionally separate. The caller must retain its HEAD
    lease and use an exact source-hash compare-and-swap when writing ``result.text``.
    If a crash happens after this append but before PLAN replacement, retrying
    safely recognizes and verifies the existing archive blocks.
    """
    if not isinstance(result, ArchiveResult):
        raise ValueError("result must be an ArchiveResult")
    if result.findings:
        raise ArchiveConflict("PLAN has archiveable tasks without valid timestamps")
    if not result.blocks:
        return ()
    root = Path(project_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("project root must be a directory")
    grouped: dict[str, list[ArchivedBlock]] = {}
    for block in result.blocks:
        if (not re.fullmatch(r"TASK-[A-Z0-9][A-Z0-9-]*", block.task_id)
                or not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", block.month)
                or block.relative_path != f"plan/archive/{block.month}.md"):
            raise ArchiveConflict("archive plan contains an invalid destination")
        grouped.setdefault(block.month, []).append(block)

    written: list[str] = []
    for month, month_blocks in sorted(grouped.items()):
        relative = PurePosixPath(f"plan/archive/{month}.md")
        destination = root.joinpath(*relative.parts)
        _ensure_safe_destination(root, destination)
        existing = destination.read_bytes().decode("utf-8") if destination.exists() else (
            f"# Plan archive {month}\n\n"
        )
        additions = []
        for block in month_blocks:
            marker = f"<!-- archive-block {block.task_id} chars="
            if marker in existing:
                try:
                    archived = read_archived_block(existing, block.task_id)
                except (KeyError, ValueError) as exc:
                    raise ArchiveConflict(
                        f"existing archive entry for {block.task_id} is malformed"
                    ) from exc
                if archived != block.block:
                    raise ArchiveConflict(
                        f"existing archive entry for {block.task_id} differs from PLAN"
                    )
                continue
            additions.append(
                f"<!-- archive-block {block.task_id} chars={len(block.block)} -->\n"
                f"{block.block}"
                f"<!-- /archive-block {block.task_id} -->\n"
            )
        if additions:
            _atomic_write(root, destination, (existing + "".join(additions)).encode("utf-8"))
            written.append(relative.as_posix())
    return tuple(written)


def read_archived_block(archive_text: str, task_id: str) -> str:
    """Recover one exact task block from a monthly archive."""
    if not re.fullmatch(r"TASK-[A-Z0-9][A-Z0-9-]*", task_id):
        raise ValueError(f"illegal task id {task_id!r}")
    marker = re.compile(
        rf"<!-- archive-block {re.escape(task_id)} chars=(\d+) -->\n"
    )
    match = marker.search(archive_text)
    if not match:
        raise KeyError(f"{task_id} is not in this archive")
    count = int(match.group(1))
    start = match.end()
    if start + count > len(archive_text):
        raise ValueError(f"{task_id}: archive block is truncated")
    block = archive_text[start:start + count]
    end_marker = f"<!-- /archive-block {task_id} -->"
    if not archive_text.startswith(end_marker, start + count):
        raise ValueError(f"{task_id}: archive block length or terminator is invalid")
    return block


def plan_notes_rotation(text: str, *, now: datetime, cap: int = 4000) -> NotesRotation:
    """Plan an orchestrator-notes overflow rotation without filesystem writes."""
    if not isinstance(text, str):
        raise ValueError("PLAN source must be text")
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise ValueError("notes cap must be a positive integer")
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("now must be a timezone-aware datetime")
    now = now.astimezone(timezone.utc)
    frontmatter = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    if frontmatter is None:
        raise ArchiveConflict("PLAN has no YAML frontmatter; refusing to rotate notes")
    lines = frontmatter.group(1).splitlines(keepends=True)
    note_positions = [index for index, line in enumerate(lines)
                      if line.rstrip("\r\n").startswith("orchestrator_notes:")]
    if len(note_positions) > 1:
        raise ArchiveConflict("PLAN has duplicate orchestrator_notes fields")
    if not note_positions:
        return NotesRotation(text, None, None, 0)
    index = note_positions[0]
    line = lines[index]
    raw = line.rstrip("\r\n").split(":", 1)[1].strip()
    notes = _decode_notes(raw)
    if len(notes) <= cap:
        return NotesRotation(text, None, None, len(notes))
    day = now.strftime("%Y-%m-%d")
    relative_path = f"docs/handovers/{day}-notes.md"
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    pointer = f"Overflow rotated to {relative_path} ({len(notes)} chars) at {stamp}."
    if len(pointer) > cap:
        raise ValueError("notes pointer exceeds the configured cap")
    ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
    prefix = line[:len(line) - len(line.lstrip())]
    lines[index] = prefix + "orchestrator_notes: " + json.dumps(pointer, ensure_ascii=False) + ending
    updated_frontmatter = "".join(lines)
    updated = text[:frontmatter.start(1)] + updated_frontmatter + text[frontmatter.end(1):]
    body = f"## Rotated {stamp}\n\n{notes}\n"
    return NotesRotation(updated, relative_path, body, len(notes))


def append_notes_rotation(project_root: str | Path,
                          rotation: NotesRotation) -> tuple[str, ...]:
    """Append a notes body once, verifying a digest marker on every retry."""
    if not isinstance(rotation, NotesRotation):
        raise ValueError("rotation must be a NotesRotation")
    if rotation.relative_path is None or rotation.body is None:
        return ()
    if not re.fullmatch(r"docs/handovers/\d{4}-\d{2}-\d{2}-notes\.md",
                        rotation.relative_path):
        raise ArchiveConflict("invalid notes rotation destination")
    root = Path(project_root).resolve(strict=True)
    destination = root.joinpath(*PurePosixPath(rotation.relative_path).parts)
    _ensure_safe_destination(root, destination)
    digest = hashlib.sha256(rotation.body.encode("utf-8")).hexdigest()
    marker = f"<!-- notes-rotation sha256={digest} chars={len(rotation.body)} -->\n"
    existing = destination.read_bytes().decode("utf-8") if destination.exists() else (
        "# Rotated plan notes\n\n"
    )
    if marker in existing:
        start = existing.index(marker) + len(marker)
        if existing[start:start + len(rotation.body)] != rotation.body:
            raise ArchiveConflict("existing notes rotation differs from planned body")
        if not existing.startswith("<!-- /notes-rotation -->", start + len(rotation.body)):
            raise ArchiveConflict("existing notes rotation is truncated")
        return ()
    addition = marker + rotation.body + "<!-- /notes-rotation -->\n"
    _atomic_write(root, destination, (existing + addition).encode("utf-8"))
    return (rotation.relative_path,)


def _decode_notes(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ArchiveConflict("orchestrator_notes is not valid JSON-quoted text") from exc
        if not isinstance(value, str):
            raise ArchiveConflict("orchestrator_notes JSON value must be a string")
        return value
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1]
    if raw in {"|", ">", "|-", ">-"}:
        raise ArchiveConflict("block-scalar orchestrator_notes is unsupported")
    return raw


def _fields(block: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in block.splitlines():
        match = _FIELD.match(line.strip())
        if match:
            values[match.group(1)] = match.group(2).strip()
    return values


def _month_of(updated_at: str) -> str | None:
    match = _UPDATED.match(updated_at.strip())
    if not match:
        return None
    year, month, day = map(int, match.groups())
    try:
        date(year, month, day)
    except ValueError:
        return None
    return f"{year:04d}-{month:02d}"


def _ensure_safe_destination(root: Path, destination: Path) -> None:
    try:
        destination.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise ArchiveConflict("archive destination escapes project root") from exc
    current = root
    for part in destination.relative_to(root).parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ArchiveConflict("archive destination contains a symlink directory")
    if destination.is_symlink():
        raise ArchiveConflict("archive destination is a symlink")


def _atomic_write(root: Path, destination: Path, content: bytes) -> None:
    _ensure_safe_destination(root, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(prefix=".codexdevteam-archive-",
                                    dir=destination.parent)
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _ensure_safe_destination(root, destination)
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)
