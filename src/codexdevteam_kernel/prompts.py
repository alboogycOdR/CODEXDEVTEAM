"""Deterministic maker/checker prompts rendered from authoritative records."""

from .protocol import TaskRecord


def render_maker_prompt(task: TaskRecord) -> str:
    """Render all task acceptance and ownership facts into the maker request."""
    if not isinstance(task, TaskRecord):
        raise ValueError("maker prompt requires an authoritative TaskRecord")
    if not task.acceptance_criteria:
        raise ValueError(f"{task.task_id} has no acceptance criteria; unattended dispatch refused")
    sections = [
        "CODEXDEVTEAM SUPERVISED MAKER TASK",
        f"Task ID: {task.task_id}",
        f"Title: {task.title}",
        "Authoritative source: the active CODEXDEVTEAM task record. Implement only this task.",
        "Acceptance criteria:",
        *[f"- {item}" for item in task.acceptance_criteria],
        "Owned_Paths (the complete write scope):",
        *[f"- {item}" for item in task.owned_paths],
        "Protected_Grants (use only where needed and only within Owned_Paths):",
        *([f"- {item}" for item in task.protected_grants]
          or ["- none"]),
        "Dependencies:",
        *([f"- {item}" for item in task.depends_on] or ["- none"]),
        "Required test or evidence notes:",
        *([f"- {item}" for item in task.test_evidence] or ["- follow repository verification policy"]),
        "Do not modify files outside Owned_Paths. Do not alter task state or shared control metadata.",
        "Implement the acceptance criteria, run only authorized checks, and report concise factual results.",
    ]
    return "\n".join(sections)


def render_checker_prompt(task: TaskRecord, *, sha: str, gate_fingerprint: str) -> str:
    """Render an exact-SHA checker request with strict JSON-only output rules."""
    if not isinstance(task, TaskRecord):
        raise ValueError("checker prompt requires an authoritative TaskRecord")
    if not isinstance(sha, str) or len(sha) not in {40, 64}:
        raise ValueError("checker prompt requires the full reviewed commit SHA")
    if not isinstance(gate_fingerprint, str) or not gate_fingerprint.strip():
        raise ValueError("checker prompt requires the registered gate fingerprint")
    criteria = "\n".join(f"- {item}" for item in task.acceptance_criteria) or "- none recorded"
    return (
        "CODEXDEVTEAM INDEPENDENT REVIEW\n"
        f"Task ID: {task.task_id}\n"
        f"Title: {task.title}\n"
        f"Commit SHA: {sha}\n"
        f"Gate fingerprint: {gate_fingerprint}\n"
        "Review exactly this committed state against the task and acceptance criteria.\n"
        "Acceptance criteria:\n" + criteria + "\n"
        "Return only one JSON object with exactly these fields: task_id, sha, "
        "gate_fingerprint, decision, rationale, evidence_refs. Use decision "
        "approved, changes_requested, or rejected. Cite concrete file/line or "
        "gate evidence in evidence_refs. Do not include markdown fences or prose outside JSON."
    )
