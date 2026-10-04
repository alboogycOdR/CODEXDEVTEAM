"""Read-only project-mode detection; detection never activates a HEAD."""

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class OnboardingMode(StrEnum):
    FRESH = "fresh"
    DEVDEPARTMENT_SIDECAR = "devdepartment_sidecar"
    CODEXDEVTEAM_UPGRADE = "codexdevteam_upgrade"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class OnboardingInspection:
    mode: OnboardingMode
    codexdevteam_present: bool
    devdepartment_present: bool
    activation_allowed: bool = False
    findings: tuple[str, ...] = ()


def inspect_project(root: str | Path) -> OnboardingInspection:
    """Inspect common framework markers without writing or acquiring authority."""
    project = Path(root).resolve()
    if not project.is_dir():
        raise ValueError("project root must be an existing directory")
    codex_marker = project / ".codexdevteam" / "installation.json"
    is_codex_repo = ((project / "src" / "codexdevteam_kernel").is_dir()
                     and (project / "ROADMAP.md").is_file())
    codex_present = codex_marker.is_file() or is_codex_repo
    dev_marker = ((project / ".devteam").exists()
                  or ((project / "PLAN.md").is_file()
                      and (project / "scripts" / "supervisor.py").is_file()))
    if codex_present and dev_marker:
        try:
            installation = json.loads(codex_marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            installation = None
        if (isinstance(installation, dict)
                and installation.get("integration_mode") == "devdepartment_sidecar"
                and installation.get("activated") is False
                and installation.get("active_head") is None):
            return OnboardingInspection(
                OnboardingMode.DEVDEPARTMENT_SIDECAR, True, True, False,
                ("CODEXDEVTEAM sidecar is installed; DEVDEPARTMENT remains incumbent until explicit handover",))
        return OnboardingInspection(OnboardingMode.CONFLICT, True, True, False,
                                    ("both development-team systems are present; inspect ownership and HEAD state",))
    if dev_marker:
        return OnboardingInspection(OnboardingMode.DEVDEPARTMENT_SIDECAR, False, True)
    if codex_present:
        return OnboardingInspection(OnboardingMode.CODEXDEVTEAM_UPGRADE, True, False)
    return OnboardingInspection(OnboardingMode.FRESH, False, False)
