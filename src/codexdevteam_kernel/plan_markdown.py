"""Read-only projection of DEVDEPARTMENT-style Markdown plans into kernel tasks."""

import re
import hashlib
from collections import Counter
from dataclasses import dataclass

from .protocol import TaskRecord
from .tasks import TaskState


_TASK_HEADER = re.compile(r"^###\s+(TASK-[A-Za-z0-9-]+)\s*$")
_FIELD = re.compile(r"^\*\*([A-Za-z_]+):\*\*\s*(.*)$")
_TASK_ID = re.compile(r"TASK-[A-Za-z0-9-]+")
_EMPTY = {"", "—", "-", "--", "n/a", "none", "null", "tbd"}
_AUTHORITATIVE_FIELDS = {
    "Title", "Status", "Assigned_To", "Priority", "Owned_Paths",
    "Protected_Grants", "Depends_On", "Acceptance_Criteria", "Test_Evidence",
    "Owns_Failure", "Task_Class", "Archived",
}


@dataclass(frozen=True, slots=True)
class ParsedPlan:
    tasks: tuple[TaskRecord, ...]
    findings: tuple[str, ...]
    archived_task_ids: tuple[str, ...] = ()
    source_field_names: tuple[tuple[str, tuple[str, ...]], ...] = ()
    source_field_conflicts: tuple[tuple[str, tuple[str, ...]], ...] = ()
    source_field_values: tuple[tuple[str, tuple[tuple[str, tuple[str, ...]], ...]], ...] = ()


class PlanWriteConflict(RuntimeError):
    """Raised when a PLAN projection cannot be updated without ambiguity."""


def patch_plan_task_state(text: str, *, task_id: str, state: TaskState,
                          assigned_worker: str | None,
                          expected_sha256: str) -> tuple[str, str]:
    """Patch only Status/Assigned_To after an exact source-hash compare.

    This is a pure compare-and-swap adapter. The caller owns persistence and
    must use an atomic file replace; unknown fields and all unrelated bytes are
    preserved. A project integration should still require an active HEAD lease.
    """
    if not isinstance(text, str):
        raise ValueError("PLAN source must be text")
    actual_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if actual_hash != expected_sha256:
        raise PlanWriteConflict("PLAN changed since it was read")
    if not isinstance(state, TaskState):
        raise ValueError("state must be a TaskState")
    if assigned_worker is not None and (
        not isinstance(assigned_worker, str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", assigned_worker)
    ):
        raise ValueError("assigned_worker must be a single safe registry ID or null")
    if not re.fullmatch(r"TASK-[A-Z0-9][A-Z0-9-]*", task_id or ""):
        raise ValueError("task_id must be a canonical TASK-* ID")

    lines = text.splitlines(keepends=True)
    headers = [(index, match.group(1).upper()) for index, line in enumerate(lines)
               if (match := _TASK_HEADER.match(line.rstrip("\r\n").strip()))]
    matches = [index for index, candidate_id in headers if candidate_id == task_id]
    if len(matches) != 1:
        raise PlanWriteConflict(f"expected one {task_id} block, found {len(matches)}")
    start = matches[0]
    end = next((index for index, _ in headers if index > start), len(lines))
    replacements = {"Status": state.value,
                    "Assigned_To": assigned_worker if assigned_worker is not None else "—"}
    field_positions: dict[str, list[int]] = {name: [] for name in replacements}
    for index in range(start + 1, end):
        field = _FIELD.match(lines[index].rstrip("\r\n").strip())
        if field and field.group(1) in field_positions:
            field_positions[field.group(1)].append(index)
    for name, positions in field_positions.items():
        if len(positions) != 1:
            raise PlanWriteConflict(f"expected one {name} field in {task_id}, found {len(positions)}")
        index = positions[0]
        line = lines[index]
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        prefix = line[:len(line) - len(line.lstrip())]
        lines[index] = f"{prefix}**{name}:** {replacements[name]}{ending}"
    updated = "".join(lines)
    return updated, hashlib.sha256(updated.encode("utf-8")).hexdigest()


def parse_plan_markdown(text: str) -> ParsedPlan:
    """Project task fields without editing or normalizing the source document.

    The original Markdown remains authoritative input for legacy projects; the
    returned records contain only the versioned kernel fields understood here.
    Unknown fields are intentionally ignored by this read adapter.
    """
    blocks: list[tuple[str, dict[str, str], tuple[str, ...], dict[str, list[str]]]] = []
    current_id: str | None = None
    fields: dict[str, str] = {}
    duplicate_fields: set[str] = set()
    field_occurrences: dict[str, list[str]] = {}
    current_field: str | None = None

    def finish() -> None:
        if current_id is not None:
            blocks.append((current_id.upper(), fields.copy(), tuple(sorted(duplicate_fields)),
                           {name: values.copy() for name, values in field_occurrences.items()}))

    for line in text.splitlines():
        header = _TASK_HEADER.match(line.strip())
        if header:
            finish()
            current_id, fields, duplicate_fields, field_occurrences, current_field = (
                header.group(1), {}, set(), {}, None)
            continue
        if current_id is None:
            continue
        field_match = _FIELD.match(line.strip())
        if field_match:
            current_field = field_match.group(1)
            field_occurrences.setdefault(current_field, []).append(field_match.group(2).strip())
            if current_field in fields:
                duplicate_fields.add(current_field)
            fields[current_field] = field_match.group(2).strip()
        elif current_field and line.strip():
            fields[current_field] += "\n" + line.strip()
            field_occurrences[current_field][-1] += "\n" + line.strip()
    finish()

    tasks: list[TaskRecord] = []
    archived_task_ids: list[str] = []
    findings: list[str] = []
    source_field_conflicts: list[tuple[str, tuple[str, ...]]] = []
    for task_id, values, duplicates, _field_occurrences in blocks:
        try:
            authoritative_duplicates = tuple(
                name for name in duplicates if name in _AUTHORITATIVE_FIELDS
            )
            non_authoritative_duplicates = tuple(
                name for name in duplicates if name not in _AUTHORITATIVE_FIELDS
            )
            if authoritative_duplicates:
                raise ValueError("duplicate fields: " + ", ".join(authoritative_duplicates))
            if non_authoritative_duplicates:
                source_field_conflicts.append((task_id, non_authoritative_duplicates))
            if "Archived" in values:
                if (set(values) != {"Status", "Archived"}
                        or _required(values, "Status").lower() != TaskState.DONE.value
                        or not re.fullmatch(
                            r"plan/archive/[0-9]{4}-(?:0[1-9]|1[0-2])\.md",
                            _required(values, "Archived"),
                        )):
                    raise ValueError(
                        "archived task stub must contain only done status and a valid archive path"
                    )
                archived_task_ids.append(task_id.upper())
                continue
            status = _required(values, "Status").lower()
            if "Assigned_To" not in values:
                raise ValueError("missing required field Assigned_To")
            assignment = values["Assigned_To"].strip()
            priority = _required(values, "Priority").lower()
            owned = _path_list(_required(values, "Owned_Paths"))
            grants = _path_list(values.get("Protected_Grants", ""))
            dependencies = tuple(_TASK_ID.findall(values.get("Depends_On", "")))
            acceptance = _bullet_items(values.get("Acceptance_Criteria", ""))
            evidence = _bullet_items(values.get("Test_Evidence", ""))
            owns_failures = tuple(_TASK_ID.findall(values.get("Owns_Failure", "")))
            tasks.append(TaskRecord(
                task_id=task_id,
                title=_required(values, "Title"),
                state=TaskState(status),
                assigned_worker=None if assignment.lower() in _EMPTY else assignment,
                priority=priority,
                owned_paths=owned,
                protected_grants=grants,
                depends_on=dependencies,
                acceptance_criteria=acceptance,
                test_evidence=evidence,
                owns_failures=owns_failures,
                task_class=(None if values.get("Task_Class", "").strip().lower() in _EMPTY
                            else values["Task_Class"].strip().lower()),
            ))
        except (ValueError, KeyError) as exc:
            findings.append(f"{task_id}: {exc}")
    duplicates = sorted(task_id for task_id, count in Counter(archived_task_ids).items()
                        if count > 1)
    findings.extend(f"{task_id}: duplicate archived task stub" for task_id in duplicates)
    source_field_names = tuple(
        (task_id, tuple(sorted(values))) for task_id, values, _, _ in blocks
    )
    source_field_values = tuple(
        (task_id, tuple((name, tuple(occurrences))
                        for name, occurrences in sorted(field_values.items())))
        for task_id, _, _, field_values in blocks
    )
    return ParsedPlan(tuple(tasks), tuple(findings), tuple(archived_task_ids),
                      source_field_names, tuple(source_field_conflicts), source_field_values)


def _required(fields: dict[str, str], name: str) -> str:
    value = fields.get(name, "").strip()
    if value.lower() in _EMPTY:
        raise ValueError(f"missing required field {name}")
    return value


def _path_list(value: str) -> tuple[str, ...]:
    if value.strip().lower() in _EMPTY:
        return ()
    items = []
    for raw in re.split(r"[,\n]", value):
        item = re.sub(r"\s+\(new\)$", "", raw.strip(), flags=re.I)
        if item and item.lower() not in _EMPTY:
            items.append(item)
    return tuple(items)


def _bullet_items(value: str) -> tuple[str, ...]:
    result = []
    for line in value.splitlines():
        item = re.sub(r"^\s*(?:[-*]|\d+\.)\s*", "", line).strip()
        item = re.sub(r"^\[[ xX]\]\s*", "", item).strip()
        if item and item.lower() not in _EMPTY:
            result.append(item)
    return tuple(result)
