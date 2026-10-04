"""Hash-bound, read-only task translation plans for controlled handover."""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Mapping

from .protocol import TaskRecord, validate_task_set
from .registry import WorkerRegistry
from .plan_markdown import parse_plan_markdown
from .state import HeadLease, StateStore
from .tasks import TaskState
from .secrets import find_secrets


HANDOVER_MAP_VERSION = 2
_ACTIVE_DISPOSITIONS = {TaskState.PENDING.value, TaskState.BLOCKED.value}
_TRANSLATED_FIELDS = {
    "Title", "Status", "Assigned_To", "Priority", "Owned_Paths",
    "Protected_Grants", "Depends_On", "Acceptance_Criteria", "Test_Evidence",
    "Owns_Failure", "Task_Class",
    "Archived",
}


@dataclass(frozen=True, slots=True)
class HandoverPreview:
    """A translated task set bound to source PLAN and an explicit task map."""

    source_plan_sha256: str
    mapping_sha256: str
    tasks: tuple[TaskRecord, ...]
    historical_task_ids: tuple[str, ...]
    archived_task_ids: tuple[str, ...]
    unmapped_source_fields: tuple[tuple[str, tuple[str, ...]], ...]
    source_field_conflicts: tuple[tuple[str, tuple[str, ...]], ...]
    source_field_dispositions: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = ()
    context_field_values: tuple[tuple[str, str, int, str], ...] = field(default=(), repr=False)
    mapping_version: int = HANDOVER_MAP_VERSION

    def to_dict(self) -> dict:
        return {
            "protocol_version": self.mapping_version,
            "source_plan_sha256": self.source_plan_sha256,
            "mapping_sha256": self.mapping_sha256,
            "tasks": [task.to_dict() for task in self.tasks],
            "historical_task_ids": list(self.historical_task_ids),
            "archived_task_ids": list(self.archived_task_ids),
            "unmapped_source_fields": {
                task_id: list(fields) for task_id, fields in self.unmapped_source_fields
            },
            "source_field_conflicts": {
                task_id: list(fields) for task_id, fields in self.source_field_conflicts
            },
            "source_field_dispositions": {
                task_id: dict(items) for task_id, items in self.source_field_dispositions
            },
            "write_performed": False,
            "activation_authorized": False,
        }


def plan_handover(source_plan: str | bytes, mapping: Mapping[str, object], *,
                  registry: WorkerRegistry) -> HandoverPreview:
    """Translate a reviewed task map without importing state or acquiring HEAD.

    Each source task must have an explicit mapping. Open work can only enter as
    pending or blocked, never as claimed, in-progress, needs-review, or done.
    Source-completed tasks remain historical because this system has no
    CODEXDEVTEAM review evidence for them.
    """
    if isinstance(source_plan, bytes):
        try:
            source_text = source_plan.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("source PLAN must be UTF-8") from exc
    elif isinstance(source_plan, str):
        source_text = source_plan
    else:
        raise ValueError("source PLAN must be text or UTF-8 bytes")
    if not isinstance(registry, WorkerRegistry):
        raise ValueError("registry must be a WorkerRegistry")
    if not isinstance(mapping, Mapping):
        raise ValueError("handover map must be an object")
    map_version = mapping.get("protocol_version")
    allowed_top = {"protocol_version", "source_plan_sha256", "tasks"}
    if map_version == HANDOVER_MAP_VERSION:
        allowed_top.add("source_field_dispositions")
    if set(mapping) != allowed_top:
        raise ValueError("handover map has missing or unsupported top-level fields")
    if (not isinstance(map_version, int) or isinstance(map_version, bool)
            or map_version not in {1, HANDOVER_MAP_VERSION}):
        raise ValueError("unsupported handover map protocol version")

    source_sha = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    if mapping.get("source_plan_sha256") != source_sha:
        raise ValueError("handover map does not match the current source PLAN hash")
    parsed = parse_plan_markdown(source_text)
    if parsed.findings:
        raise ValueError("source PLAN has findings: " + "; ".join(parsed.findings))
    task_mappings = mapping.get("tasks")
    if (not isinstance(task_mappings, Mapping)
            or not all(isinstance(key, str) for key in task_mappings)):
        raise ValueError("handover map tasks must be an object")
    source_by_id = {task.task_id: task for task in parsed.tasks}
    if set(task_mappings) != set(source_by_id):
        missing = sorted(set(source_by_id) - set(task_mappings))
        unknown = sorted(set(task_mappings) - set(source_by_id))
        details = []
        if missing:
            details.append("missing mappings: " + ", ".join(missing))
        if unknown:
            details.append("unknown task mappings: " + ", ".join(unknown))
        raise ValueError("handover map task coverage is incomplete: " + "; ".join(details))

    translated: list[TaskRecord] = []
    historical: list[str] = []
    canonical_mappings: dict[str, dict] = {}
    for task_id in sorted(source_by_id):
        source = source_by_id[task_id]
        item = task_mappings[task_id]
        if not isinstance(task_id, str) or not isinstance(item, Mapping):
            raise ValueError("each handover task mapping must be an object keyed by task ID")
        disposition = item.get("disposition")
        if source.state is TaskState.DONE:
            if set(item) != {"disposition"} or disposition != "historical":
                raise ValueError(f"completed source task {task_id} must map only to historical")
            historical.append(task_id)
            canonical_mappings[task_id] = {"disposition": "historical"}
            continue
        if disposition != "open" or set(item) != {
                "disposition", "state", "assigned_worker", "protected_grants"}:
            raise ValueError(f"open source task {task_id} requires a complete open-task mapping")
        target_state = item.get("state")
        if not isinstance(target_state, str) or target_state not in _ACTIVE_DISPOSITIONS:
            raise ValueError(f"open task {task_id} must map to pending or blocked")
        worker_id = item.get("assigned_worker")
        if worker_id is not None:
            if not isinstance(worker_id, str) or worker_id not in registry.active:
                raise ValueError(f"mapped worker for {task_id} must be an active registry identity")
            worker = registry.resolve(worker_id)
            if worker.control_mode != "strict":
                raise ValueError(f"mapped worker for {task_id} is not strict-verified")
            maker_identity = {"unit_id": worker.identity.unit_id,
                              "runtime": worker.identity.runtime,
                              "model": worker.identity.model}
        else:
            maker_identity = None
        raw_grants = item.get("protected_grants")
        if (not isinstance(raw_grants, list)
                or not all(isinstance(grant, str) and grant.strip() for grant in raw_grants)):
            raise ValueError(f"mapped Protected_Grants for {task_id} must be a string array")
        grants = tuple(raw_grants)
        translated.append(TaskRecord(
            task_id=task_id,
            title=source.title,
            state=TaskState(target_state),
            assigned_worker=worker_id,
            priority=source.priority,
            owned_paths=source.owned_paths,
            protected_grants=grants,
            depends_on=source.depends_on,
            acceptance_criteria=source.acceptance_criteria,
            test_evidence=source.test_evidence,
            maker_identity=maker_identity,
            owns_failures=source.owns_failures,
            task_class=source.task_class,
        ))
        canonical_mappings[task_id] = {
            "disposition": "open", "state": target_state,
            "assigned_worker": worker_id, "protected_grants": list(grants),
        }

    historical_ids = set(historical) | set(parsed.archived_task_ids)
    unmapped_fields = tuple(
        (task_id, tuple(name for name in field_names if name not in _TRANSLATED_FIELDS))
        for task_id, field_names in parsed.source_field_names
        if any(name not in _TRANSLATED_FIELDS for name in field_names)
    )
    field_dispositions: dict[str, dict[str, str]] = {}
    if map_version == HANDOVER_MAP_VERSION:
        raw_dispositions = mapping.get("source_field_dispositions")
        expected_fields = {task_id: set(fields) for task_id, fields in unmapped_fields}
        if (not isinstance(raw_dispositions, Mapping)
                or set(raw_dispositions) != set(expected_fields)):
            raise ValueError("source_field_dispositions must cover exactly the source-only PLAN tasks")
        for task_id, fields in expected_fields.items():
            item = raw_dispositions[task_id]
            if not isinstance(item, Mapping) or set(item) != fields:
                raise ValueError(f"source-field dispositions for {task_id} must cover exactly: "
                                 + ", ".join(sorted(fields)))
            if any(not isinstance(value, str) or value not in {"preserve", "exclude"}
                   for value in item.values()):
                raise ValueError("source-field dispositions must be 'preserve' or 'exclude'")
            field_dispositions[task_id] = {name: item[name] for name in sorted(fields)}
    source_values = {task_id: dict(items) for task_id, items in parsed.source_field_values}
    context_values = tuple(
        (task_id, field_name, occurrence, value)
        for task_id, decisions in sorted(field_dispositions.items())
        for field_name, decision in sorted(decisions.items()) if decision == "preserve"
        for occurrence, value in enumerate(source_values[task_id][field_name], start=1)
    )
    validation_tasks = tuple(
        task if task.state not in {TaskState.PENDING, TaskState.BLOCKED}
        else TaskRecord.from_dict({**task.to_dict(), "state": TaskState.IN_PROGRESS.value})
        for task in translated
    )
    findings = validate_task_set(validation_tasks, archived_task_ids=tuple(sorted(historical_ids)))
    if findings:
        raise ValueError("translated task set is invalid: " + "; ".join(findings))
    mapping_sha = hashlib.sha256(json.dumps(
        {"protocol_version": map_version, "source_plan_sha256": source_sha,
         "tasks": canonical_mappings,
         "source_field_dispositions": field_dispositions},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return HandoverPreview(
        source_sha, mapping_sha, tuple(translated), tuple(sorted(historical_ids)),
        tuple(sorted(set(parsed.archived_task_ids))), unmapped_fields,
        parsed.source_field_conflicts,
        tuple((task_id, tuple(sorted(items.items())))
              for task_id, items in sorted(field_dispositions.items())),
        context_values, map_version,
    )


def stage_handover(source_plan: str | bytes, mapping: Mapping[str, object], *,
                   registry: WorkerRegistry, store: StateStore, lease: HeadLease,
                   event_id: str, now: float | None = None) -> HandoverPreview:
    """Lease-fence an explicitly dispositioned, inactive import after revalidating source bytes.

    Unknown legacy fields must be explicitly preserved as opaque context or
    excluded from target task context. Preserved values remain outside the
    authoritative TaskRecord schema. This operation never verifies incumbent
    process fencing and never authorizes activation.
    """
    preview = plan_handover(source_plan, mapping, registry=registry)
    _validate_stageable_preview(preview)
    if not isinstance(store, StateStore) or not isinstance(lease, HeadLease):
        raise ValueError("handover import requires a StateStore and current HEAD lease")
    current_bytes = (source_plan if isinstance(source_plan, bytes)
                     else source_plan.encode("utf-8"))
    current_sha = hashlib.sha256(current_bytes).hexdigest()
    if current_sha != preview.source_plan_sha256:
        raise ValueError("source PLAN changed while preparing handover import")
    store.import_handover_tasks(
        lease, preview.tasks, preview.historical_task_ids,
        context_fields=preview.context_field_values,
        source_plan_sha256=preview.source_plan_sha256,
        mapping_sha256=preview.mapping_sha256, event_id=event_id, now=now,
    )
    return preview


def _validate_stageable_preview(preview: HandoverPreview) -> None:
    decisions = {task_id: dict(items) for task_id, items in preview.source_field_dispositions}
    for task_id, fields in preview.unmapped_source_fields:
        if set(decisions.get(task_id, {})) != set(fields):
            raise ValueError("handover import requires explicit disposition for every source-only field")
    for task_id, field_name, _occurrence, value in preview.context_field_values:
        if find_secrets(value):
            raise ValueError(f"secret-like content in preserved field {field_name} for {task_id} was refused")


def handover_map_template(source_plan: str | bytes) -> dict:
    """Build an incomplete, hash-bound mapping draft without writing files."""
    if isinstance(source_plan, bytes):
        try:
            source_text = source_plan.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("source PLAN must be UTF-8") from exc
    elif isinstance(source_plan, str):
        source_text = source_plan
    else:
        raise ValueError("source PLAN must be text or UTF-8 bytes")
    parsed = parse_plan_markdown(source_text)
    if parsed.findings:
        raise ValueError("source PLAN has findings: " + "; ".join(parsed.findings))
    tasks = {}
    source_fields = dict(parsed.source_field_names)
    source_dispositions = {}
    for task in parsed.tasks:
        if task.state is TaskState.DONE:
            tasks[task.task_id] = {"disposition": "historical"}
        else:
            tasks[task.task_id] = {
                "disposition": "", "state": "", "assigned_worker": None,
                "protected_grants": [],
            }
    for task_id, names in source_fields.items():
        unknown = sorted(name for name in names if name not in _TRANSLATED_FIELDS)
        if unknown:
            source_dispositions[task_id] = {name: "" for name in unknown}
    return {
        "protocol_version": HANDOVER_MAP_VERSION,
        "source_plan_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "tasks": tasks,
        "source_field_dispositions": source_dispositions,
    }
