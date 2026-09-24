"""Root ``untaped skills …`` command group and the per-run skills check.

The root exposes commands over the union of the shell plus every composed capability's
skills. Selection, planning, and install machinery are imported from
:mod:`untaped.skills`; only the short-selector rule is new: a selector
naming no skill exactly retries with the ``untaped-`` prefix, while the
installed directory and marker always keep the full ``untaped-*`` ID.

:func:`check_installed_skills` runs after every root command: installed
skills that no longer match this version are reported (or, with
``skills.updates: auto``, updated in place) so agents do not follow stale
instructions.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from untaped.batch import batch_apply, finish
from untaped.capabilities.registry import ApplicationSpec, CompositionResult
from untaped.cli import (
    ColumnsOption,
    DryRunOption,
    FormatOption,
    YesOption,
    create_app,
    echo,
    emit,
    existing_directory,
    raise_usage,
    report_errors,
)
from untaped.errors import ConfigError, UntapedError, UsageError
from untaped.messages import hint, not_found, plural
from untaped.render import OutputFormat
from untaped.settings import SkillsSettings, load_settings_section
from untaped.skills import (
    AllSkillsOption,
    InstallableSkill,
    InstalledSkill,
    SkillForceOption,
    SkillInstallScope,
    SkillInstallTarget,
    SkillNamesArgument,
    SkillProjectDirOption,
    SkillScopeOption,
    SkillState,
    SkillStdinOption,
    SkillTargetDirOption,
    SkillTargetOption,
    find_installed_skills,
    install_skills,
    outdated_skills,
    project_root,
    remove_installed_skill,
    skill_roots,
    skill_rows,
    update_installed_skill,
)
from untaped.stdin import read_identifiers
from untaped.ui import ui_context

_INSTALLED_KIND = "untaped.installed_skill"
_OUTCOME_KIND = "untaped.skill_outcome"

CheckOption = Annotated[
    bool,
    Parameter(
        name="--check",
        negative="",
        help="Exit 3 when an installed skill is outdated or no longer shipped.",
    ),
]
RemoveTargetOption = Annotated[
    SkillInstallTarget,
    Parameter(name="--target", help="Agent target to remove from."),
]
RemoveScopeOption = Annotated[
    SkillInstallScope | None,
    Parameter(name="--scope", help="Remove only from this scope (default: both)."),
]
FoundProjectDirOption = Annotated[
    Path | None,
    Parameter(
        name="--project-dir",
        help="Project whose local skills to include (default: the current git root).",
        validator=existing_directory,
    ),
]


def composed_skills(
    shell: ApplicationSpec, result: CompositionResult
) -> dict[str, InstallableSkill]:
    """Return the shell's skills plus every composed capability's, keyed by name."""
    skills_map: dict[str, InstallableSkill] = {asset.name: asset for asset in shell.skills}
    for registered in result.capabilities:
        for asset in registered.skills:
            skills_map[asset.name] = asset
    return skills_map


def build_root_skills_app(*, shell: ApplicationSpec, result: CompositionResult) -> App:
    """Return the root ``skills`` command group for one composition."""
    skills_map = composed_skills(shell, result)
    app = create_app(
        name="skills",
        help=f"List, install, update, and remove agent skills shipped by {shell.name}.",
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
        /,
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

    @app.command(name="status")
    def status_command(
        skill_names: SkillNamesArgument = None,
        /,
        *,
        stdin: SkillStdinOption = False,
        project_dir: FoundProjectDirOption = None,
        check: CheckOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Show installed skills and whether each matches this version."""
        with report_errors():
            found = _select_installed(
                skills_map, list(skill_names or []), stdin=stdin, project_dir=project_dir
            )
            emit(
                [_installed_row(item) for item in found],
                fmt=fmt,
                columns=columns,
                kind=_INSTALLED_KIND,
                empty="No installed skills found.",
            )
        finish(False, predicate_hit=check and any(_stale(item) for item in found))

    @app.command(name="update")
    def update_command(
        skill_names: SkillNamesArgument = None,
        /,
        *,
        stdin: SkillStdinOption = False,
        project_dir: FoundProjectDirOption = None,
        dry_run: DryRunOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Update installed skills that differ from this version (default: all)."""
        _update(
            skills_map,
            list(skill_names or []),
            stdin=stdin,
            project_dir=project_dir,
            dry_run=dry_run,
            fmt=fmt,
            columns=columns,
        )

    @app.command(name="remove")
    def remove_command(
        skill_names: SkillNamesArgument = None,
        /,
        *,
        stdin: SkillStdinOption = False,
        all_skills: Annotated[
            bool,
            Parameter(name="--all", negative="", help="Remove every installed untaped skill."),
        ] = False,
        target: RemoveTargetOption = SkillInstallTarget.all,
        scope: RemoveScopeOption = None,
        project_dir: FoundProjectDirOption = None,
        yes: YesOption = False,
        dry_run: DryRunOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Remove installed skills; only directories untaped installed are touched."""
        if not skill_names and not stdin and not all_skills:
            raise_usage("provide skill names, --stdin, or --all")
        _remove(
            skills_map,
            list(skill_names or []),
            stdin=stdin,
            all_skills=all_skills,
            target=target,
            scope=scope,
            project_dir=project_dir,
            yes=yes,
            dry_run=dry_run,
            fmt=fmt,
            columns=columns,
        )

    return app


def check_installed_skills(skills: Mapping[str, InstallableSkill]) -> None:
    """Report installed skills that no longer match this version.

    Runs after every root command. ``skills.updates`` picks the behaviour:
    ``warn`` (default) prints a warning, ``auto`` updates outdated skills in
    place, ``off`` does nothing. Skills this version no longer ships are only
    ever reported: removing them is left to ``untaped skills remove``.
    """
    mode = _updates_mode()
    if mode == "off":
        return
    stale = outdated_skills(skills, project_dir=project_root(Path.cwd()))
    if not stale:
        return
    ui = ui_context(strict=False)
    outdated = [item for item in stale if item.state is SkillState.outdated]
    if mode == "auto" and outdated:
        failed: list[InstalledSkill] = []
        for item in outdated:
            try:
                update_installed_skill(skills, item)
            except UntapedError:
                failed.append(item)
        updated = len(outdated) - len(failed)
        if updated:
            ui.message("info", f"updated {plural(updated, 'outdated skill')}")
        outdated = failed
    if outdated:
        ui.message("warning", f"installed skills are out of date: {_names(outdated)}")
        echo(
            f"{hint('skills update')} (set skills.updates to auto or off to change this)",
            err=True,
        )
    orphaned = [item for item in stale if item.state is SkillState.orphaned]
    if orphaned:
        names = _names(orphaned)
        ui.message("warning", f"installed skills are no longer shipped: {names}")
        echo(hint(f"skills remove {names.replace(',', '')}"), err=True)


def _updates_mode() -> str:
    try:
        settings = load_settings_section("skills")
    except UntapedError, ValueError:
        return "warn"
    return settings.updates if isinstance(settings, SkillsSettings) else "warn"


def _names(items: list[InstalledSkill]) -> str:
    return ", ".join(sorted({item.name for item in items}))


def _stale(item: InstalledSkill) -> bool:
    return item.state is not SkillState.current


def _installed_row(item: InstalledSkill) -> dict[str, object]:
    return {
        "name": item.name,
        "target": item.target,
        "scope": item.scope.value,
        "state": item.state.value,
        "target_path": str(item.path),
    }


def _found(
    skills: Mapping[str, InstallableSkill], *, project_dir: Path | None
) -> list[InstalledSkill]:
    root = project_dir.expanduser().resolve() if project_dir else project_root(Path.cwd())
    return find_installed_skills(skills, skill_roots(project_dir=root))


def _select_installed(
    skills: Mapping[str, InstallableSkill],
    skill_names: list[str],
    *,
    stdin: bool,
    project_dir: Path | None,
    all_skills: bool = False,
) -> list[InstalledSkill]:
    """Return installed skills, narrowed to the selected names when any are given."""
    if int(bool(skill_names)) + int(stdin) + int(all_skills) > 1:
        raise UsageError("provide skill names, --stdin, or --all; not more than one")
    found = _found(skills, project_dir=project_dir)
    if all_skills or (not skill_names and not stdin):
        return found
    installed = {item.name: item for item in found}
    selected: list[str] = []
    for selector in read_identifiers(skill_names, stdin=stdin):
        name = _resolve_short(selector, installed)
        if name not in installed:
            raise ConfigError(not_found("installed skill", selector, known=sorted(installed)))
        selected.append(name)
    return [item for item in found if item.name in selected]


def _update(
    skills: Mapping[str, InstallableSkill],
    skill_names: list[str],
    *,
    stdin: bool,
    project_dir: Path | None,
    dry_run: bool,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    with report_errors():
        found = _select_installed(skills, skill_names, stdin=stdin, project_dir=project_dir)
        named = bool(skill_names) or stdin
        rows: list[dict[str, object]] = []
        failed = False
        ui = ui_context(strict=False)
        for item in found:
            if item.state is SkillState.current:
                if named:
                    rows.append(_outcome_row(item, "unchanged"))
                continue
            if item.state is SkillState.orphaned:
                if named:
                    ui.message("error", f"{item.name}: skill no longer shipped")
                    rows.append(_outcome_row(item, "failed"))
                    failed = True
                continue
            if dry_run:
                rows.append(_outcome_row(item, "planned"))
                continue
            try:
                update_installed_skill(skills, item)
            except UntapedError as exc:
                ui.message("error", f"{item.name}: {exc}")
                rows.append(_outcome_row(item, "failed"))
                failed = True
                continue
            rows.append(_outcome_row(item, "updated"))
        emit(rows, fmt=fmt, columns=columns, kind=_OUTCOME_KIND, empty="No outdated skills found.")
        updated = sum(1 for row in rows if row["action"] == "updated")
        if updated:
            ui.success(f"updated {plural(updated, 'skill')}")
    finish(failed)


def _remove(
    skills: Mapping[str, InstallableSkill],
    skill_names: list[str],
    *,
    stdin: bool,
    all_skills: bool,
    target: SkillInstallTarget,
    scope: SkillInstallScope | None,
    project_dir: Path | None,
    yes: bool,
    dry_run: bool,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    with report_errors():
        found = _select_installed(
            skills, skill_names, stdin=stdin, project_dir=project_dir, all_skills=all_skills
        )
        chosen = [
            item
            for item in found
            if target in (SkillInstallTarget.all, item.target) and scope in (None, item.scope)
        ]
        outcome = batch_apply(
            chosen,
            remove_installed_skill,
            verb="remove",
            noun="skill",
            label=lambda item: str(item.path),
            describe=lambda item: _outcome_row(item, "planned"),
            ui=ui_context(strict=False),
            destructive=True,
            assume_yes=yes,
            preview_only=dry_run,
        )
        if outcome.cancelled:
            finish(outcome)
        rows = (
            outcome.planned_rows
            if dry_run
            else [_outcome_row(item, "deleted") for item, _ in outcome.results]
        )
        emit(rows, fmt=fmt, columns=columns, kind=_OUTCOME_KIND, empty="No installed skills found.")
        if outcome.results:
            ui_context(strict=False).success(f"removed {plural(len(outcome.results), 'skill')}")
    finish(outcome)


def _outcome_row(item: InstalledSkill, action: str) -> dict[str, object]:
    return {
        "name": item.name,
        "target": item.target,
        "scope": item.scope.value,
        "target_path": str(item.path),
        "action": action,
    }


def _resolve_short(selector: str, skills: Mapping[str, object]) -> str:
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
        emit(skill_rows(skills), fmt=fmt, columns=columns, kind="untaped.skill")


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
            raise UsageError("provide skill names, --stdin, or --all; not more than one")
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
            on_installed=lambda result: ui_context(strict=False).success(
                f"installed skill: {result.name}"
            ),
        )


__all__ = ["build_root_skills_app", "check_installed_skills", "composed_skills"]
