"""Factory for the ``<tool> skills …`` command group.

``run_tool`` calls :func:`build_skills_app` and mounts the result, so each tool
exposes ``skills list / install`` over its OWN packaged skills (``spec.skills``,
a tuple of :class:`~untaped.tool.SkillAsset`) instead of the plugin registry.

The selection, planning, and install machinery lives in :mod:`untaped.skills`;
this module supplies the skill source from the tool's own assets (rather than a
plugin registry) and renders/exits.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from untaped.cli import (
    ColumnsOption,
    FormatOption,
    create_app,
    echo,
    raise_usage,
    render_rows,
    report_errors,
)
from untaped.output import OutputFormat
from untaped.skills import (
    AllSkillsOption,
    InstallableSkill,
    SkillForceOption,
    SkillInstallScope,
    SkillInstallTarget,
    SkillNamesArgument,
    SkillProjectDirOption,
    SkillScopeOption,
    SkillStdinOption,
    SkillTargetDirOption,
    SkillTargetOption,
    _install_skill,
    _install_targets,
    _plan_install,
    _selected_skill_names,
    skill_rows,
)
from untaped.tool import ToolSpec
from untaped.ui import ui_context


def build_skills_app(spec: ToolSpec) -> Any:
    """Return the cyclopts ``skills`` command group for ``spec``."""
    skills_map: dict[str, InstallableSkill] = {asset.name: asset for asset in spec.skills}
    app = create_app(
        name="skills",
        help=f"List and install agent skills shipped by {spec.command}.",
    )

    @app.command(name="list")
    def list_command(
        *,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """List the agent skills this tool ships."""
        _list(skills_map, fmt=fmt, columns=columns)

    @app.command(name="install")
    def install_command(
        skill_names: SkillNamesArgument = None,
        *,
        stdin: SkillStdinOption = False,
        all_skills: AllSkillsOption = False,
        target: SkillTargetOption = SkillInstallTarget.codex,
        force: SkillForceOption = False,
        scope: SkillScopeOption = SkillInstallScope.global_,
        project_dir: SkillProjectDirOption = None,
        target_dir: SkillTargetDirOption = None,
    ) -> None:
        """Install this tool's skills into an agent skill directory."""
        if not skill_names and not stdin and not all_skills:
            raise_usage("provide skill names, --stdin, or --all")
        _install(
            skills_map,
            list(skill_names or []),
            stdin=stdin,
            all_skills=all_skills,
            target=target,
            force=force,
            scope=scope,
            project_dir=project_dir,
            target_dir=target_dir,
        )

    return app


def _list(
    skills: dict[str, InstallableSkill],
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    with report_errors():
        rendered = render_rows(skill_rows(skills), fmt=fmt, columns=columns)
        if rendered:
            echo(rendered)


def _install(
    skills: dict[str, InstallableSkill],
    skill_names: list[str],
    *,
    stdin: bool,
    all_skills: bool,
    target: SkillInstallTarget,
    force: bool,
    scope: SkillInstallScope,
    project_dir: Path | None,
    target_dir: Path | None,
) -> None:
    with report_errors():
        selected_names = _selected_skill_names(
            skills,
            skill_names,
            stdin=stdin,
            all_skills=all_skills,
        )
        targets = _install_targets(
            target,
            scope=scope,
            project_dir=project_dir,
            target_dir=target_dir,
        )
        install_plan = _plan_install(skills, selected_names, targets, force=force)
        for asset, target_destination, destination in install_plan:
            _install_skill(
                asset,
                target_destination=target_destination,
                destination=destination,
                force=force,
            )
            ui_context(strict=False).message("success", f"installed skill: {asset.name}")


__all__ = ["build_skills_app"]
