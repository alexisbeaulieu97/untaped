"""Installing skills in one call, as ``skills install`` does without its rows."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from untaped.core.skills import (
    PackagedSkill,
    SkillInstallResult,
    SkillInstallScope,
    SkillInstallTarget,
    install_planned,
    plan_install,
)


def install_skills(
    skills: Mapping[str, PackagedSkill],
    skill_names: list[str],
    *,
    all_skills: bool,
    target: SkillInstallTarget,
    force: bool,
    scope: SkillInstallScope,
    project_dir: Path | None,
    target_dir: Path | None,
) -> list[SkillInstallResult]:
    """Install the selected skills after planning them all."""
    plan = plan_install(
        skills,
        skill_names,
        all_skills=all_skills,
        target=target,
        force=force,
        scope=scope,
        project_dir=project_dir,
        target_dir=target_dir,
    )
    return [install_planned(planned, force=force) for planned in plan]
