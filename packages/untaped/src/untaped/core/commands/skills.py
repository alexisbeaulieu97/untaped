"""Root ``untaped skills …`` command group and the per-run skills check.

The root exposes commands over the union of the shell plus every composed plugin's
skills. Selection, planning, and install machinery are imported from
:mod:`untaped.core.skills`; only the short-selector rule is new: a selector
naming no skill exactly retries with the ``untaped-`` prefix, while the
installed directory and marker always keep the full ``untaped-*`` ID.

:func:`check_installed_skills` runs after every root command. Under
``skills.updates: auto`` (the default) it refreshes the stub skills in the
home folder that an older untaped wrote and converts copies from 11.0 to
stubs, keeping the copies under ``~/.untaped/backups/skills``; ``warn``
reports them instead. A project's own skills are never written by a run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from untaped.core import skill_stubs
from untaped.core.commands.records import InstalledSkillRow, SkillOutcome, SkillRow
from untaped.core.skills import (
    AllSkillsOption,
    InstalledSkill,
    PackagedSkill,
    SkillForceOption,
    SkillInstallScope,
    SkillInstallTarget,
    SkillKind,
    SkillNamesArgument,
    SkillProjectDirOption,
    SkillScopeOption,
    SkillState,
    SkillStdinOption,
    SkillTargetDirOption,
    SkillTargetOption,
    find_installed_skills,
    install_skills,
    local_project_root,
    remove_installed_skill,
    skill_backups_dir,
    skill_roots,
    skill_rows,
    update_installed_skill,
)
from untaped.runtime.registry import ApplicationSpec, CompositionResult
from untaped.runtime.settings import SkillsSettings, load_settings_section
from untaped.runtime.sink import Diagnostics
from untaped.runtime.theme import OutputFormat
from untaped.sdk._batch import batch_apply, finish
from untaped.sdk._cli import (
    ColumnsOption,
    DryRunOption,
    FormatOption,
    YesOption,
    create_app,
    emit,
    existing_directory,
    raise_usage,
    report_errors,
    writes,
)
from untaped.sdk._stdin import read_identifiers
from untaped.sdk._ui import ui_context
from untaped.sdk.diagnostic import Failure
from untaped.sdk.errors import ConfigError, UntapedError, UsageError
from untaped.sdk.words import not_found, plural, run_hint, shown_path

_diag = Diagnostics()


CheckOption = Annotated[
    bool,
    Parameter(
        name="--check",
        negative="",
        help="Exit 3 when an installed skill is outdated or shipped by no installed plugin.",
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
UpdateForceOption = Annotated[
    bool,
    Parameter(
        name="--force",
        negative="",
        help="Also rewrite stubs someone edited or a newer untaped wrote.",
    ),
]
FoundProjectDirOption = Annotated[
    Path | None,
    Parameter(
        name="--project-dir",
        help="Project whose local skills to include (default: the current git root).",
        validator=existing_directory,
    ),
]


def composed_skills(shell: ApplicationSpec, result: CompositionResult) -> dict[str, PackagedSkill]:
    """Return the shell's skills plus every composed plugin's, keyed by name."""
    skills_map = {
        asset.name: PackagedSkill(asset, plugin=shell.name, distribution=skill_stubs.CORE)
        for asset in shell.skills
    }
    for registered in result.plugins:
        ref = registered.plugin_ref
        for asset in registered.skills:
            skills_map[asset.name] = PackagedSkill(
                asset,
                plugin=registered.spec.name,
                distribution=ref.distribution,
                version=ref.distribution_version,
            )
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
        """List the agent skills shipped by every composed plugin."""
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
                empty="No installed skills found.",
            )
        finish(False, predicate_hit=check and any(_stale(item) for item in found))

    @app.command(name="update")
    @writes
    def update_command(
        skill_names: SkillNamesArgument = None,
        /,
        *,
        stdin: SkillStdinOption = False,
        project_dir: FoundProjectDirOption = None,
        force: UpdateForceOption = False,
        dry_run: DryRunOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Update installed skills that an older untaped wrote (default: all).

        A stub a newer untaped wrote, or one someone edited, is left alone
        unless ``--force`` is given.
        """
        _update(
            skills_map,
            list(skill_names or []),
            stdin=stdin,
            project_dir=project_dir,
            force=force,
            dry_run=dry_run,
            fmt=fmt,
            columns=columns,
        )

    @app.command(name="remove")
    @writes(destructive=True)
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


def check_installed_skills(
    skills: Mapping[str, PackagedSkill], *, allow_updates: bool = True
) -> None:
    """Keep the agent skills in the home folder current; report a project's.

    Runs after every root command. ``skills.updates`` picks the behaviour
    for the home roots (``~/.agents/skills``, ``~/.claude/skills``):
    ``auto`` (default) silently refreshes stubs an older untaped wrote,
    converts copies from 11.0 to stubs (keeping each copy under
    ``~/.untaped/backups/skills``) and updates other outdated copies; ``warn``
    reports them; ``off`` does nothing. Without ``allow_updates`` (after a
    failed or previewing command) nothing is written: ``auto`` leaves stubs
    and copies to convert for the next successful command and warns about
    other copies. A project's own roots
    are never written: old copies there are reported, stubs never. Skills no
    composed plugin ships are left to ``untaped doctor``.
    """
    mode = _updates_mode()
    if mode == "off":
        return
    found = find_installed_skills(
        skills, skill_roots(project_dir=local_project_root(None)), deep=False
    )
    sorted_ = _sort_outdated(skills, found)
    older, copies = sorted_.older, sorted_.copies
    if mode == "auto" and not allow_updates:
        older = []  # refreshed after the next command that succeeds
    elif mode == "auto":
        older = _refresh(skills, older)
        copies = _update_copies(skills, copies)
    hint = f"{run_hint('skills update')} (set skills.updates to auto or off to change this)"
    here = f"{run_hint('skills update')} here (or set skills.updates to off)"
    for items, message, hint_text in (
        (older, "agent skills from an older untaped", hint),
        (copies, "installed skills are out of date", hint),
        (sorted_.old_in_project, "copied skills from an older untaped in this project", here),
        (sorted_.stale_in_project, "copied skills in this project are out of date", here),
    ):
        if items:
            _diag.warning(f"{message}: {_names(items)}")
            _diag.hint(hint_text)


@dataclass(frozen=True)
class _Outdated:
    """The per-run check's outdated skills, by what it may do about each."""

    older: list[InstalledSkill]  # home stubs this run is newer than, and copies to convert
    copies: list[InstalledSkill]  # home copies a newer copy replaces
    old_in_project: list[InstalledSkill]  # project copies a stub replaces
    stale_in_project: list[InstalledSkill]  # project copies a newer copy replaces


def _sort_outdated(skills: Mapping[str, PackagedSkill], found: list[InstalledSkill]) -> _Outdated:
    """Sort outdated skills; a project's stubs drop out."""
    sorted_ = _Outdated([], [], [], [])
    for item in found:
        if item.state is not SkillState.outdated:
            continue
        to_stub = item.kind is SkillKind.copy and skills[item.name].stub
        if item.home:
            (sorted_.older if item.kind is SkillKind.stub or to_stub else sorted_.copies).append(
                item
            )
        elif item.kind is SkillKind.copy:
            (sorted_.old_in_project if to_stub else sorted_.stale_in_project).append(item)
    return sorted_


def _refresh(
    skills: Mapping[str, PackagedSkill], items: list[InstalledSkill]
) -> list[InstalledSkill]:
    """Rewrite stubs and convert copies; return the ones that failed."""
    failed: list[InstalledSkill] = []
    converted = 0
    for item in items:
        backup = _backup_root(skills, item)
        try:
            update_installed_skill(skills, item, backup_root=backup)
        except UntapedError:
            failed.append(item)
            continue
        converted += backup is not None
    if converted:
        # Unmuted: it shows once, and names where the old copies went.
        _diag.info(_converted_line(converted), unmuted=True)
    return failed


def _backup_root(skills: Mapping[str, PackagedSkill], item: InstalledSkill) -> Path | None:
    """Where a home-folder copy goes when it converts to a stub; a project has its VCS."""
    converts = item.kind is SkillKind.copy and skills[item.name].stub
    return skill_backups_dir() if converts and item.home else None


def _converted_line(count: int) -> str:
    return (
        f"converted {plural(count, 'agent skill')} to stubs; "
        f"the old copies are in {shown_path(skill_backups_dir())}"
    )


def _update_copies(
    skills: Mapping[str, PackagedSkill], items: list[InstalledSkill]
) -> list[InstalledSkill]:
    """Replace outdated copies with this version's; return the ones that failed."""
    failed: list[InstalledSkill] = []
    for item in items:
        try:
            update_installed_skill(skills, item)
        except UntapedError:
            failed.append(item)
    updated = len(items) - len(failed)
    if updated:
        _diag.info(f"updated {plural(updated, 'outdated skill')}")
    return failed


_DEFAULT_MODE: str = SkillsSettings.model_fields["updates"].default


def _updates_mode() -> str:
    try:
        settings = load_settings_section("skills")
    except UntapedError, ValueError:
        return _DEFAULT_MODE
    return settings.updates if isinstance(settings, SkillsSettings) else _DEFAULT_MODE


def _names(items: list[InstalledSkill]) -> str:
    return ", ".join(sorted({item.name for item in items}))


def _stale(item: InstalledSkill) -> bool:
    return item.state in (SkillState.outdated, SkillState.orphaned)


def _installed_row(item: InstalledSkill) -> InstalledSkillRow:
    return InstalledSkillRow(
        name=item.name,
        target=item.target,
        scope=item.scope.value,
        state=item.state.value,
        kind=item.kind.value,
        target_path=item.path,
    )


def _found(
    skills: Mapping[str, PackagedSkill], *, project_dir: Path | None
) -> list[InstalledSkill]:
    return find_installed_skills(skills, skill_roots(project_dir=local_project_root(project_dir)))


def _select_installed(
    skills: Mapping[str, PackagedSkill],
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
            raise ConfigError(
                not_found("installed skill", selector, known=sorted(installed)),
                category="not_found",
            )
        selected.append(name)
    return [item for item in found if item.name in selected]


def _update(
    skills: Mapping[str, PackagedSkill],
    skill_names: list[str],
    *,
    stdin: bool,
    project_dir: Path | None,
    force: bool,
    dry_run: bool,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    with report_errors():
        found = _select_installed(skills, skill_names, stdin=stdin, project_dir=project_dir)
        named = bool(skill_names) or stdin
        rows: list[SkillOutcome] = []
        failed = False
        converted = 0
        for item in found:
            if item.state in (SkillState.newer, SkillState.edited) and not force:
                why = (
                    f"written by a newer {item.written_by}"
                    if item.state is SkillState.newer
                    else "edited since it was installed; --force replaces it"
                )
                _diag.info(f"left {item.name}: {why}")
                rows.append(_outcome(item, "skipped"))
                continue
            if item.state is SkillState.current:
                if named:
                    rows.append(_outcome(item, "unchanged"))
                continue
            if item.state is SkillState.orphaned:
                if named:
                    error = _diag.failure(
                        Failure.plain("no plugin installed here ships this skill"), item=item.name
                    )
                    rows.append(_outcome(item, "failed", error=error))
                    failed = True
                continue
            if dry_run:
                rows.append(_outcome(item, "planned"))
                continue
            backup = _backup_root(skills, item)
            try:
                update_installed_skill(skills, item, backup_root=backup)
            except UntapedError as exc:
                rows.append(_outcome(item, "failed", error=_diag.failure(exc, item=item.name)))
                failed = True
                continue
            rows.append(_outcome(item, "updated"))
            converted += backup is not None
        emit(rows, fmt=fmt, columns=columns, empty="No outdated skills found.")
        updated = sum(1 for row in rows if row.action == "updated")
        if updated:
            _diag.success(f"updated {plural(updated, 'skill')}")
        if converted:
            _diag.info(_converted_line(converted), unmuted=True)
    finish(failed)


def _remove(
    skills: Mapping[str, PackagedSkill],
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
            describe=lambda item: _outcome(item, "planned").model_dump(mode="json"),
            ui=ui_context(strict=False),
            destructive=True,
            assume_yes=yes,
            preview_only=dry_run,
        )
        if outcome.cancelled:
            finish(outcome)
        rows = (
            [_outcome(item, "planned") for item in chosen]
            if dry_run
            else [_outcome(item, "deleted") for item, _ in outcome.results]
        )
        emit(rows, fmt=fmt, columns=columns, empty="No installed skills found.")
        if outcome.results:
            _diag.success(f"removed {plural(len(outcome.results), 'skill')}")
    finish(outcome)


def _outcome(item: InstalledSkill, action: str, *, error: Failure | None = None) -> SkillOutcome:
    return SkillOutcome(
        name=item.name,
        target=item.target,
        scope=item.scope.value,
        target_path=item.path,
        action=action,
        error=error,
    )


def _resolve_short(selector: str, skills: Mapping[str, object]) -> str:
    """Resolve one skill selector, accepting the ``untaped-``-less short form."""
    if selector in skills:
        return selector
    prefixed = f"untaped-{selector}"
    if prefixed in skills:
        return prefixed
    return selector


def _list(
    skills: dict[str, PackagedSkill],
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    with report_errors():
        emit([SkillRow.model_validate(row) for row in skill_rows(skills)], fmt=fmt, columns=columns)


def _install(
    skills: dict[str, PackagedSkill],
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
            on_installed=lambda result: _diag.success(f"installed skill: {result.name}"),
        )


__all__ = ["build_root_skills_app", "check_installed_skills", "composed_skills"]
