"""Root ``untaped skills …`` command group (Wave 1.4).

Converts the per-tool skills surface (:mod:`untaped.skills_app`) to root
commands over the union of the shell plus every composed capability's
skills. Selection, planning, and install machinery are imported from
:mod:`untaped.skills`; only the short-selector rule is new: a selector
naming no skill exactly retries with the ``untaped-`` prefix, while the
installed directory and marker always keep the full ``untaped-*`` ID.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from cyclopts import App

from untaped.capabilities.registry import ApplicationSpec, CompositionResult
from untaped.cli import (
    ColumnsOption,
    FormatOption,
    create_app,
    emit,
    raise_usage,
    report_errors,
)
from untaped.errors import ConfigError
from untaped.render import OutputFormat
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
    install_skills,
    skill_rows,
)
from untaped.stdin import read_identifiers
from untaped.ui import ui_context


def build_root_skills_app(*, shell: ApplicationSpec, result: CompositionResult) -> App:
    """Return the root ``skills`` command group for one composition."""
    skills_map: dict[str, InstallableSkill] = {asset.name: asset for asset in shell.skills}
    for registered in result.capabilities:
        for asset in registered.skills:
            skills_map[asset.name] = asset
    app = create_app(
        name="skills",
        help=f"List and install agent skills shipped by {shell.name}.",
    )

    @app.command(name="list")
    def list_command(
        *,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """List the agent skills shipped by every composed capability."""
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
        """Install composed skills into an agent skill directory."""
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


def _resolve_short(selector: str, skills: Mapping[str, InstallableSkill]) -> str:
    """Resolve one skill selector, accepting the ``untaped-``-less short form."""
    if selector in skills:
        return selector
    prefixed = f"untaped-{selector}"
    if prefixed in skills:
        return prefixed
    return selector


def _list(
    skills: dict[str, InstallableSkill],
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    with report_errors():
        emit(skill_rows(skills), fmt=fmt, columns=columns)


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
        if int(bool(skill_names)) + int(stdin) + int(all_skills) > 1:
            raise ConfigError("provide skill names, --stdin, or --all; not more than one")
        if all_skills:
            selected = sorted(skills)
        else:
            selected = [
                _resolve_short(name, skills) for name in read_identifiers(skill_names, stdin=stdin)
            ]
        install_skills(
            skills,
            selected,
            stdin=False,
            all_skills=False,
            target=target,
            force=force,
            scope=scope,
            project_dir=project_dir,
            target_dir=target_dir,
            on_installed=lambda result: ui_context(strict=False).message(
                "success", f"installed skill: {result.name}"
            ),
        )


__all__ = ["build_root_skills_app"]
