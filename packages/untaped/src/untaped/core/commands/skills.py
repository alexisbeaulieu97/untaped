"""Root ``untaped skills …`` command group and the per-run skills check.

The root exposes commands over the union of the shell plus every composed plugin's
skills. Selection, planning, and install machinery are imported from
:mod:`untaped.core.skills`; only the short-selector rule is new: a selector
naming no skill exactly retries with the ``untaped-`` prefix, while the
installed directory and marker always keep the full ``untaped-*`` ID.

:func:`check_installed_skills` runs after every root command. Under
``untaped.skills.updates: auto`` (the default) it refreshes the stub skills in the
home folder that an older untaped wrote; ``warn`` reports them instead. A
project's own skills are never written by a run.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path

from pydantic import Field

from untaped.core import skill_stubs
from untaped.core.commands import records
from untaped.core.commands.records import SkillOutcome
from untaped.core.settings import CoreSettings
from untaped.core.skills import (
    InstalledSkill,
    PackagedSkill,
    SkillInstallScope,
    SkillInstallTarget,
    SkillState,
    find_installed_skills,
    install_skills,
    local_project_root,
    remove_installed_skill,
    skill_roots,
    skill_rows,
    update_installed_skill,
)
from untaped.runtime.registry import CompositionResult, SkillAsset
from untaped.runtime.settings import SkillsSettings, load_core_part
from untaped.runtime.sink import Diagnostics
from untaped.sdk._stdin import read_identifiers
from untaped.sdk.diagnostic import Failure
from untaped.sdk.errors import ConfigError, Matched, UntapedError, UsageError
from untaped.sdk.fields import Confirm, DryRun, Output, Positional
from untaped.sdk.plugin import Command, Group
from untaped.sdk.words import not_found, plural, run_hint, shown_path

_diag = Diagnostics()


def composed_skills(result: CompositionResult) -> dict[str, PackagedSkill]:
    """Return core's skill plus every composed plugin's, keyed by name.

    Core's stub is named ``untaped``, after core itself; a plugin's is
    ``untaped-<plugin>``.
    """
    skills_map: dict[str, PackagedSkill] = {}
    core = result.mount.declaration.plugin(skill_stubs.CORE)
    if core.skill:
        asset = SkillAsset(name=skill_stubs.CORE, description=core.skill)
        skills_map[asset.name] = PackagedSkill(
            asset, plugin=skill_stubs.CORE, distribution=skill_stubs.CORE
        )
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


def _skills() -> dict[str, PackagedSkill]:
    # Lazy: bootstrap is the CLI's root and imports this module.
    from untaped.runtime.bootstrap import composition  # noqa: PLC0415

    return composed_skills(composition())


_NAMES = "Skill names (the untaped- prefix is optional)."
_STDIN = "Read skill names from stdin."
_PROJECT = "Project whose local skills to include (default: the current git root)."


class List(Command[CoreSettings]):
    """List the agent skills shipped by every composed plugin.

    Examples:
        $ untaped skills list
    """

    repairs = True
    output: Output[records.Skill]

    def run(self) -> Iterator[records.Skill]:
        for row in skill_rows(_skills()):
            yield records.Skill.model_validate(row)


class Install(Command[CoreSettings]):
    """Install composed skills into an agent skill directory.

    Examples:
        $ untaped skills install --all --target claude
        $ untaped skills install awx --scope local
    """

    repairs = True
    skill_names: Positional[list[str]] = Field(default_factory=list, description=_NAMES)
    stdin: bool = Field(False, description=_STDIN)
    all: bool = Field(False, description="Install every registered skill.")
    target: SkillInstallTarget = Field(
        SkillInstallTarget.codex, description="Agent target to install for."
    )
    force: bool = Field(False, description="Replace existing target skill dirs.")
    scope: SkillInstallScope = Field(SkillInstallScope.global_, description="Install scope.")
    project_dir: Path | None = Field(None, description="Project directory for local installs.")
    target_dir: Path | None = Field(
        None, description="Override the selected target's skills directory."
    )

    def run(self) -> None:
        if not self.skill_names and not self.stdin and not self.all:
            raise UsageError("provide skill names, --stdin, or --all")
        _existing(self.project_dir)
        _install(
            _skills(),
            list(self.skill_names),
            stdin=self.stdin,
            all_skills=self.all,
            target=self.target,
            force=self.force,
            scope=self.scope,
            project_dir=self.project_dir,
            target_dir=self.target_dir,
        )


class Status(Command[CoreSettings]):
    """Show installed skills and whether each matches this version.

    Examples:
        $ untaped skills status
        $ untaped skills status --check
    """

    repairs = True
    skill_names: Positional[list[str]] = Field(default_factory=list, description=_NAMES)
    stdin: bool = Field(False, description=_STDIN)
    project_dir: Path | None = Field(None, description=_PROJECT)
    check: bool = Field(
        False,
        description="Exit 3 when an installed skill is outdated or shipped by no installed plugin.",
    )
    output: Output[records.InstalledSkill]

    def run(self) -> Iterator[records.InstalledSkill]:
        _existing(self.project_dir)
        self.output.empty("No installed skills found.")
        found = _select_installed(
            _skills(), list(self.skill_names), stdin=self.stdin, project_dir=self.project_dir
        )
        yield from (_installed_row(item) for item in found)
        if self.check and any(_stale(item) for item in found):
            raise Matched("an installed skill is outdated or shipped by no installed plugin")


class Update(Command[CoreSettings]):
    """Update installed skills that an older untaped wrote (default: all).

    A stub a newer untaped wrote, or one someone edited, is left alone unless
    --force is given.

    Examples:
        $ untaped skills update --dry-run
        $ untaped skills update untaped-awx --force
    """

    repairs = True
    skill_names: Positional[list[str]] = Field(default_factory=list, description=_NAMES)
    stdin: bool = Field(False, description=_STDIN)
    project_dir: Path | None = Field(None, description=_PROJECT)
    force: bool = Field(
        False, description="Also rewrite stubs someone edited or a newer untaped wrote."
    )
    dry_run: DryRun
    output: Output[SkillOutcome]

    def run(self) -> Iterator[SkillOutcome]:
        _existing(self.project_dir)
        self.output.empty("No outdated skills found.")
        yield from _update(
            _skills(),
            list(self.skill_names),
            stdin=self.stdin,
            project_dir=self.project_dir,
            force=self.force,
            dry_run=bool(self.dry_run),
        )


class Remove(Command[CoreSettings]):
    """Remove installed skills; only directories untaped installed are touched.

    Examples:
        $ untaped skills remove untaped-awx --dry-run
        $ untaped skills remove --all --target claude --yes
    """

    repairs = True
    skill_names: Positional[list[str]] = Field(default_factory=list, description=_NAMES)
    stdin: bool = Field(False, description=_STDIN)
    all: bool = Field(False, description="Remove every installed untaped skill.")
    target: SkillInstallTarget = Field(
        SkillInstallTarget.all, description="Agent target to remove from."
    )
    scope: SkillInstallScope | None = Field(
        None, description="Remove only from this scope (default: both)."
    )
    project_dir: Path | None = Field(None, description=_PROJECT)
    confirm: Confirm
    output: Output[SkillOutcome]

    def run(self) -> Iterator[SkillOutcome]:
        if not self.skill_names and not self.stdin and not self.all:
            raise UsageError("provide skill names, --stdin, or --all")
        _existing(self.project_dir)
        self.output.empty("No installed skills found.")
        yield from _remove(
            _skills(),
            list(self.skill_names),
            stdin=self.stdin,
            all_skills=self.all,
            target=self.target,
            scope=self.scope,
            project_dir=self.project_dir,
            confirm=self.confirm,
        )


class Skills(Group):
    """List, install, update, and remove agent skills shipped by untaped."""

    commands = (List, Install, Status, Update, Remove)


def _existing(path: Path | None) -> None:
    """Refuse a --project-dir that is not a directory, as the option's check did."""
    if path is not None and not path.is_dir():
        raise UsageError(f"--project-dir: not a directory: {shown_path(path)}")


def check_installed_skills(
    skills: Mapping[str, PackagedSkill], *, allow_updates: bool = True
) -> None:
    """Keep the agent skills in the home folder current.

    Runs after every root command. ``untaped.skills.updates`` picks the behaviour
    for the home roots (``~/.agents/skills``, ``~/.claude/skills``):
    ``auto`` (default) silently refreshes stubs an older untaped wrote;
    ``warn`` reports them; ``off`` does nothing. Without ``allow_updates``
    (after a failed or previewing command) nothing is written: ``auto``
    leaves them for the next successful command. A project's own stubs are
    never written or reported. Skills no composed plugin ships are left to
    ``untaped doctor``.
    """
    mode = _updates_mode()
    if mode == "off":
        return
    found = find_installed_skills(
        skills, skill_roots(project_dir=local_project_root(None)), deep=False
    )
    older = [item for item in found if item.state is SkillState.outdated and item.home]
    if mode == "auto":
        if not allow_updates:
            return  # refreshed after the next command that succeeds
        older = _refresh(skills, older)
    if older:
        _diag.warning(f"agent skills from an older untaped: {_names(older)}")
        change = "set untaped.skills.updates to auto or off to change this"
        _diag.hint(f"{run_hint('skills update')} ({change})")


def _refresh(
    skills: Mapping[str, PackagedSkill], items: list[InstalledSkill]
) -> list[InstalledSkill]:
    """Rewrite stubs; return the ones that failed."""
    failed: list[InstalledSkill] = []
    for item in items:
        try:
            update_installed_skill(skills, item)
        except UntapedError:
            failed.append(item)
    return failed


_DEFAULT_MODE: str = SkillsSettings.model_fields["updates"].default


def _updates_mode() -> str:
    try:
        return load_core_part("skills").updates
    except UntapedError, ValueError:
        return _DEFAULT_MODE


def _names(items: list[InstalledSkill]) -> str:
    return ", ".join(sorted({item.name for item in items}))


def _stale(item: InstalledSkill) -> bool:
    return item.state in (SkillState.outdated, SkillState.orphaned)


def _installed_row(item: InstalledSkill) -> records.InstalledSkill:
    return records.InstalledSkill(
        name=item.name,
        target=item.target,
        scope=item.scope.value,
        state=item.state.value,
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
) -> Iterator[SkillOutcome]:
    found = _select_installed(skills, skill_names, stdin=stdin, project_dir=project_dir)
    named = bool(skill_names) or stdin
    rows: list[SkillOutcome] = []
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
            continue
        if dry_run:
            rows.append(_outcome(item, "planned"))
            continue
        try:
            update_installed_skill(skills, item)
        except UntapedError as exc:
            rows.append(_outcome(item, "failed", error=_diag.failure(exc, item=item.name)))
            continue
        rows.append(_outcome(item, "updated"))
    yield from rows
    updated = sum(1 for row in rows if row.action == "updated")
    if updated:
        _diag.success(f"updated {plural(updated, 'skill')}")


def _remove(
    skills: Mapping[str, PackagedSkill],
    skill_names: list[str],
    *,
    stdin: bool,
    all_skills: bool,
    target: SkillInstallTarget,
    scope: SkillInstallScope | None,
    project_dir: Path | None,
    confirm: Confirm,
) -> Iterator[SkillOutcome]:
    found = _select_installed(
        skills, skill_names, stdin=stdin, project_dir=project_dir, all_skills=all_skills
    )
    chosen = [
        item
        for item in found
        if target in (SkillInstallTarget.all, item.target) and scope in (None, item.scope)
    ]
    if not chosen:
        return
    question = f"Remove {plural(len(chosen), 'skill')}?"
    if not confirm(question, preview=lambda: [str(item.path) for item in chosen]):
        yield from (_outcome(item, "planned") for item in chosen)
        return
    removed = 0
    for item in chosen:
        try:
            remove_installed_skill(item)
        except UntapedError as exc:
            yield _outcome(item, "failed", error=_diag.failure(exc, item=item.name))
            continue
        removed += 1
        yield _outcome(item, "deleted")
    if removed:
        _diag.success(f"removed {plural(removed, 'skill')}")


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


__all__ = ["Skills", "check_installed_skills", "composed_skills"]
