"""Validated brief-to-PLAN authoring for a fresh, inactive CODEXDEVTEAM project."""

import json
from pathlib import Path
from typing import Any

from .dispatch import TaskClassPolicy
from .plan_markdown import parse_plan_markdown
from .protocol import validate_task_set
from .territory import decide_write


_PLAN_FIELDS = {
    "task_id", "title", "priority", "owned_paths", "protected_grants",
    "depends_on", "acceptance_criteria", "test_evidence", "task_class",
}


def render_plan_prompt(brief: str, *, task_routing: TaskClassPolicy,
                       fallback_role: str) -> str:
    """Ask the configured HEAD candidate for a strict, unassigned task plan."""
    if not isinstance(brief, str) or not brief.strip():
        raise ValueError("project brief must be non-empty text")
    if not isinstance(task_routing, TaskClassPolicy):
        raise ValueError("planner requires validated task-class routing")
    routing_lines = [
        f"- {name}: minimum capability {floor}; worker role "
        f"{task_routing.role_for(name, fallback=fallback_role)}"
        for name, floor in sorted(task_routing.capability_floors.items())
    ]
    return (
        "CODEXDEVTEAM FRESH PROJECT PLAN\n"
        "Create an implementation plan from the project brief below. Return only one JSON object "
        "with exactly the top-level fields protocol_version and tasks. Set protocol_version to 1. "
        "Each task must have exactly these fields: task_id, title, priority, owned_paths, "
        "protected_grants, depends_on, acceptance_criteria, test_evidence, task_class. Use canonical IDs "
        "TASK-<UPPERCASE-NUMBER-OR-NAME>; priorities critical, high, medium, or low; and "
        "repository-relative POSIX path patterns. Each task must have at least one owned path, "
        "at least one observable acceptance criterion, and at least one verification/evidence "
        "item. Select each task_class from the configured classes below; dependencies must "
        "refer to tasks in this plan. Keep tasks independently "
        "reviewable and order dependencies so foundational work precedes dependent work. "
        "Do not assign workers, start work, modify files, or include commentary.\n\n"
        "CONFIGURED TASK CLASSES\n" + "\n".join(routing_lines) + "\n\n"
        "PROJECT BRIEF\n" + brief.strip()
    )


def validate_plan_response(response: str, *,
                           allowed_task_classes: set[str] | None = None) -> str:
    """Validate strict JSON against the kernel task protocol and render canonical PLAN Markdown."""
    if not isinstance(response, str) or not response.strip():
        raise ValueError("planner response must be non-empty text")
    try:
        payload = json.loads(response, object_pairs_hook=_unique_object)
    except json.JSONDecodeError as exc:
        raise ValueError(f"planner response is not strict JSON: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != {"protocol_version", "tasks"}:
        raise ValueError("planner response must contain exactly protocol_version and tasks")
    if payload["protocol_version"] != 1:
        raise ValueError("planner response uses an unsupported protocol_version")
    raw_tasks = payload["tasks"]
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("planner response must contain a non-empty tasks array")

    records: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_tasks, start=1):
        if not isinstance(raw, dict) or set(raw) != _PLAN_FIELDS:
            raise ValueError(f"task {index} must contain exactly the documented task fields")
        for name in ("task_id", "title", "priority"):
            value = raw[name]
            if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
                raise ValueError(f"task {index} {name} must be one non-empty line of text")
        task_class = raw["task_class"]
        if (not isinstance(task_class, str) or not task_class.strip()
                or "\n" in task_class or "\r" in task_class):
            raise ValueError(f"task {index} task_class must be one configured class")
        if allowed_task_classes is not None and task_class not in allowed_task_classes:
            raise ValueError(f"task {index} uses unconfigured task class {task_class!r}")
        for name in ("owned_paths", "protected_grants", "depends_on",
                     "acceptance_criteria", "test_evidence"):
            value = raw[name]
            if not isinstance(value, list) or not all(
                    isinstance(item, str) and item.strip() and "\n" not in item
                    and "\r" not in item for item in value):
                raise ValueError(f"task {index} {name} must be an array of single-line non-empty strings")
            if name in {"owned_paths", "protected_grants", "depends_on"} and any("," in item for item in value):
                raise ValueError(f"task {index} {name} entries cannot contain commas in PLAN Markdown")
        if not raw["acceptance_criteria"]:
            raise ValueError(f"task {index} requires acceptance criteria")
        if not raw["test_evidence"]:
            raise ValueError(f"task {index} requires verification/evidence items")
        if not raw["owned_paths"]:
            raise ValueError(f"task {index} requires at least one owned path")
        records.append(raw)

    rendered = [
        "# Project Plan", "", "## Execution Order", "",
        "Tasks are pending and unassigned until the explicitly activated supervisor dispatches them.", "",
    ]
    for raw in records:
        rendered.extend([
            f"### {raw['task_id']}",
            f"**Title:** {raw['title']}",
            "**Status:** pending",
            "**Assigned_To:** —",
            f"**Priority:** {raw['priority']}",
            f"**Owned_Paths:** {', '.join(raw['owned_paths'])}",
            f"**Protected_Grants:** {', '.join(raw['protected_grants']) or '—'}",
            f"**Depends_On:** {', '.join(raw['depends_on']) or '—'}",
            f"**Task_Class:** {raw['task_class']}",
            "**Acceptance_Criteria:**",
            *[f"- {item}" for item in raw["acceptance_criteria"]],
            "**Test_Evidence:**",
            *[f"- {item}" for item in raw["test_evidence"]],
            "",
        ])
    markdown = "\n".join(rendered)
    parsed = parse_plan_markdown(markdown)
    if parsed.findings or not parsed.tasks:
        raise ValueError("generated PLAN failed kernel parsing: " + "; ".join(parsed.findings))
    findings = validate_task_set(parsed.tasks)
    if findings:
        raise ValueError("generated task set failed kernel validation: " + "; ".join(findings))
    if any(task.assigned_worker is not None for task in parsed.tasks):
        raise ValueError("generated fresh plan must leave every task unassigned")
    protected_control_paths = (".git/config", ".codexdevteam/state/state.sqlite", "PLAN.md")
    if any(decide_write(path, task.owned_paths).allowed
           for task in parsed.tasks for path in protected_control_paths):
        raise ValueError("generated plan ownership overlaps Git, CODEXDEVTEAM control, or PLAN files")
    _validate_dependency_graph(parsed.tasks)
    return markdown


def publish_plan_once(project_root: str | Path, markdown: str) -> Path:
    """Create PLAN.md without replacing any pre-existing project plan."""
    root = Path(project_root).resolve(strict=True)
    plan_path = root / "PLAN.md"
    if plan_path.is_symlink() or plan_path.exists():
        raise ValueError("PLAN.md already exists or is a symlink; refusing to overwrite project state")
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("validated plan content must be non-empty")
    # Exclusive creation makes the no-overwrite rule atomic against another writer.
    with plan_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(markdown)
    return plan_path


def _validate_dependency_graph(tasks: tuple) -> None:
    by_id = {task.task_id: task for task in tasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise ValueError(f"dependency cycle includes {task_id}")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in by_id[task_id].depends_on:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in by_id:
        visit(task_id)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"planner response contains duplicate JSON key {key!r}")
        result[key] = value
    return result
