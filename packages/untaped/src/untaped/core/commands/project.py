"""``untaped project …``: make, list, move, remove and prune projects.

A project is a folder whose ``.untaped/`` holds ``project.json``
(:mod:`untaped.sdk.projects`); the home records each one in ``refs/``, which is
what ``project list`` reads. ``remove`` and ``prune`` release a project's
holds through their owners (:mod:`untaped.core.project_lifecycle`), previewed
and confirmed; ``move`` moves a folder and has each owner repair it.
"""

from __future__ import annotations

import os
from collections.abc import Generator, Iterator, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from untaped.core.commands.records import ProjectOutcome, RecordedProject
from untaped.core.settings import CoreSettings
from untaped.sdk.errors import ConfigError, Failed, Matched, UntapedError, UsageError
from untaped.sdk.fields import Confirm, Output, Positional, key, table_row
from untaped.sdk.marks import experimental
from untaped.sdk.plugin import Command, Group
from untaped.sdk.projects import (
    Project,
    current_project,
    init,
    project_at,
    project_references,
    writing,
)
from untaped.sdk.storage.holds import HolderView, HoldOutcome, unnamed, view
from untaped.sdk.words import plural, shown_path

if TYPE_CHECKING:
    from untaped.core.project_lifecycle import Removal
    from untaped.sdk._run import Diag
    from untaped.sdk.storage._refs import Reference

_FORCE = (
    "Override the refusal for this item (repeatable): it is released anyway, "
    "or, when its plugin isn't installed, the hold is forgotten."
)
_CHECK = "Preview, and exit 3 when a hold is refused."


class Init(Command[CoreSettings]):
    """Make a folder a project: its .untaped/ holds project.json, and this home records it.

    Creates the folder when it doesn't exist. Run again in a project, it
    brings the project's record up to date: records it in this home, follows
    a move, or gives a copy its own id. Refuses ~ and ~/.untaped, and a
    folder inside another project or holding a recorded one.

    Examples:
        $ untaped project init
        $ untaped project init ~/work/PROJ-123
    """

    path: Positional[Path | None] = Field(
        None, description="The folder to make a project; the current folder when left out."
    )
    new_id: bool = Field(
        False, description="Give a copied project its own id (never the folder its record names)."
    )
    output: Output[ProjectOutcome]

    def run(self) -> Iterator[ProjectOutcome]:
        done = init(self.path if self.path is not None else Path.cwd(), new_id=self.new_id)
        if done.action == "unchanged":
            self.ctx.diag.info(done.detail)
        else:
            self.ctx.diag.success(done.detail)
        yield ProjectOutcome(
            name=done.project.name,
            path=str(done.project.root),
            id=done.project.id,
            action=done.action,
            detail=done.detail,
        )


class List(Command[CoreSettings]):
    """List the projects this home records, with each one's state.

    A project is present, unreadable (its drive or share isn't there, so it
    keeps what it holds) or missing (its folder is gone). current marks the
    one the working directory is in.

    Examples:
        $ untaped project list
        $ untaped project list -f json
    """

    output: Output[RecordedProject]

    def run(self) -> Iterator[RecordedProject]:
        self.output.empty("No projects yet. Make one with `untaped project init` in its folder.")
        try:
            here = project_at(Path.cwd())
        except ConfigError as exc:  # a damaged marker here: still list the others
            self.ctx.diag.warning(str(exc), hint=exc.hint)
            here = None
        for ref in project_references():
            yield _recorded(ref, here)


def _recorded(ref: Reference, here: Project | None) -> RecordedProject:
    seen = view(ref)
    if not isinstance(seen, HolderView):
        return RecordedProject(
            name="", path=str(ref.file), state="unknown", current=False, detail=seen.label
        )
    holds: dict[str, list[str]] = {}
    for target in ref.targets:
        if target.item is not None:
            holds.setdefault(f"{target.plugin}.{target.store}", []).append(target.item)
    return RecordedProject(
        name=seen.anchor.name,
        path=str(seen.anchor),
        state=seen.state,
        current=_is(here, seen),
        id=seen.holder.name,
        holds=holds,
        detail=None if seen.state == "present" else _why(seen),
    )


def _is(here: Project | None, seen: HolderView) -> bool:
    if here is None or here.id != seen.holder.name:
        return False
    try:
        return os.path.samefile(here.root, seen.anchor)
    except OSError:
        return False


def _why(seen: HolderView) -> str:
    if seen.state == "missing":
        return f"{shown_path(seen.anchor)} is gone"
    return f"{shown_path(seen.anchor)} can't be read now; it keeps what it holds"


class Move(Command[CoreSettings]):
    """Move a project's folder, or make its record follow a folder moved by hand.

    With OLD there and NEW not, moves the folder (on one filesystem only;
    across filesystems, `mv` it yourself and run this again). With OLD gone
    and NEW holding the project, its record follows. With OLD gone and NEW
    holding no project.json (a folder renamed by hand), NEW becomes the
    project after a confirmation. Each plugin the project holds items of
    then repairs what points into the folder; a repair that fails is retried
    by the next command that changes the project.

    Examples:
        $ untaped project move ~/work/PROJ-200 ~/archive/PROJ-200
        $ untaped project move ~/old/PROJ-7 ~/work/PROJ-7 --dry-run
    """

    old: Positional[Path] = Field(description="Where the project's folder is, or was.")
    new: Positional[Path] = Field(description="Where it goes, or where it already is.")
    confirm: Confirm
    output: Output[HoldOutcome]

    def run(self) -> Iterator[HoldOutcome]:
        from untaped.core.project_lifecycle import (  # noqa: PLC0415 - runs only here
            apply_move,
            plan_move,
        )

        move = plan_move(self.old, self.new)
        question = (
            f"Make {shown_path(move.new)} project {move.project.name} "
            f"(its record names {shown_path(move.old)})?"
        )
        if not self.confirm(question, needed=move.how == "adopt"):
            yield from move.preview()
            return
        rows = apply_move(move)
        yield from rows
        _report_failures(self.ctx.diag, rows)


class Remove(Command[CoreSettings]):
    """Remove a project: release what it holds, then delete what untaped wrote in it.

    Each plugin the project holds items of says how it lets go (a repo
    another project holds is kept), shown first. One refused hold applies
    nothing (exit 1); --force ITEM overrides that item's refusal. Then
    project.json, plugins/ and its own lock file go; config.yml and
    your own files stay, and so does the folder unless nothing else is in
    it. Always confirms; without a terminal it needs --yes (else exit 2).

    Examples:
        $ untaped project remove --dry-run
        $ untaped project remove ~/work/PROJ-123 --force github.com/acme/web
    """

    project: Positional[str | None] = Field(
        None, description="The project: its folder, or its name; the current project when left out."
    )
    force: list[str] = Field(default_factory=list, description=_FORCE)
    check: bool = Field(False, description=_CHECK)
    confirm: Confirm
    output: Output[HoldOutcome]

    def run(self) -> Iterator[HoldOutcome]:
        from untaped.core.project_lifecycle import (  # noqa: PLC0415 - runs only here
            find_project,
            plan_removal,
            preview_check,
            unlink_lock,
        )

        confirm = self.confirm.as_dry_run() if self.check else self.confirm
        found = current_project() if self.project is None else find_project(self.project)
        with ExitStack() as stack:
            if isinstance(found, Project):
                stack.enter_context(found.locked())
                if confirm.dry_run:
                    preview_check(found)
                else:
                    found = writing(found)[0]
            elif found.holder is not None:
                stack.enter_context(
                    Project(id=found.holder.name, root=found.anchor or Path()).locked()
                )
            removal = plan_removal(found, force=self.force)
            rows = yield from _release(
                self,
                [removal],
                confirm,
                question=f"Remove project {removal.name}?",
                verb="removed",
            )
        unlink_lock(removal.holder)
        _refused(rows)


class Prune(Command[CoreSettings]):
    """Remove the projects whose folder is gone, releasing what they hold.

    A project whose folder can't be read now (a drive or share that isn't
    there) is listed and skipped: it keeps what it holds. Previewed and
    confirmed as `project remove`; one refused hold applies nothing.

    Examples:
        $ untaped project prune --dry-run
        $ untaped project prune --yes
    """

    project: Positional[str | None] = Field(
        None,
        description="One missing project, by its folder or name; every missing one when left out.",
    )
    force: list[str] = Field(default_factory=list, description=_FORCE)
    check: bool = Field(False, description=_CHECK)
    confirm: Confirm
    # it acts on several projects at once
    output: Output[HoldOutcome] = Output(
        columns=(
            key(HoldOutcome).holder,
            key(HoldOutcome).action,
            key(HoldOutcome).plugin,
            key(HoldOutcome).item,
            key(HoldOutcome).detail,
            key(HoldOutcome).hint,
        )
    )

    def run(self) -> Iterator[HoldOutcome]:
        from untaped.core.project_lifecycle import (  # noqa: PLC0415 - runs only here
            find_project,
            missing_projects,
            plan_removal,
            unlink_lock,
        )

        confirm = self.confirm.as_dry_run() if self.check else self.confirm
        diag = self.ctx.diag
        if self.project is None:
            missing, unreadable = missing_projects()
            for ref in unreadable:
                where = shown_path(ref.anchor) if ref.anchor is not None else str(ref.file)
                diag.info(f"skipped {where}: it can't be read now, so it keeps what it holds")
        else:
            named = find_project(self.project)
            if isinstance(named, Project):
                raise UntapedError(
                    f"project {named.name} is at {shown_path(named.root)}; prune removes only "
                    "projects whose folder is gone",
                    hint="remove it with `untaped project remove`",
                )
            missing = [named]
        if not missing:
            diag.info("no project's folder is gone; nothing to prune")
            return
        with ExitStack() as stack:
            removals: list[Removal] = []
            for ref in missing:
                if ref.holder is None:  # missing_projects lists only readable records
                    continue
                stack.enter_context(Project(id=ref.holder.name, root=ref.anchor or Path()).locked())
                try:
                    removals.append(plan_removal(ref, force=self.force))
                except UntapedError as exc:  # back, or out of reach, since it was listed
                    if self.project is not None:
                        raise
                    diag.info(f"skipped: {exc}")
            if not removals:
                diag.info("nothing to prune")
                return
            rows = yield from _release(
                self,
                removals,
                confirm,
                question=f"Prune {plural(len(removals), 'project')}?",
                verb="pruned",
            )
        for removal in removals:
            unlink_lock(removal.holder)
        _refused(rows)


def _release(
    command: Remove | Prune,
    removals: Sequence[Removal],
    confirm: Confirm,
    *,
    question: str,
    verb: str,
) -> Generator[HoldOutcome, None, list[HoldOutcome]]:
    """The holds service's steps over the planned removals: preview, refuse, confirm, apply.

    Returns the rows applied (none under ``--dry-run``).
    """
    from untaped.core.project_lifecycle import apply_removal, blocked_hint  # noqa: PLC0415
    from untaped.sdk._cli import render_rows  # noqa: PLC0415 - rendering is heavy

    plans = [plan for removal in removals for plan in removal.plans]
    unknown = unnamed(command.force, plans)
    if unknown:
        raise UsageError(
            f"--force names {', '.join(unknown)}, which "
            f"{'is' if len(unknown) == 1 else 'are'} not held here",
            hint="name items as the rows show them",
        )
    planned = [row for removal in removals for row in removal.preview()]
    blocked = [plan for plan in plans if plan.blocked]
    if blocked and not confirm.dry_run:
        yield from planned
        raise UntapedError(
            f"{plural(len(blocked), 'hold')} refused; nothing {verb}",
            hint=blocked_hint(blocked),
        )
    columns = list(command.output.columns)
    # the planned rows are what --dry-run prints, so only a question shows them first
    preview = lambda: [  # noqa: E731
        render_rows([table_row(row) for row in planned], fmt="table", table_columns=columns)
    ]
    if not confirm(question, preview=None if confirm.dry_run else preview):
        yield from planned
        if command.check and blocked:
            raise Matched(f"{plural(len(blocked), 'hold')} refused")
        return []
    rows = [row for removal in removals for row in apply_removal(removal)]
    yield from rows
    _report_failures(command.ctx.diag, rows)
    return rows


def _refused(rows: Sequence[HoldOutcome]) -> None:
    """A hold refused while applying fails the run; a failed row counts on its own."""
    if any(row.action == "refused" for row in rows):
        raise Failed()


def _report_failures(diag: Diag, rows: Sequence[HoldOutcome]) -> None:
    for row in rows:
        if row.error is not None:
            diag.failure(row.error, item=f"{row.plugin} {row.item}")
        elif row.action == "refused":
            diag.warning(f"{row.plugin} {row.item}: {row.detail}", hint=row.hint)


class ProjectGroup(Group):
    """Make a folder a project and list the projects this home records."""

    stability = experimental
    commands = (Init, List, Move, Remove, Prune)


__all__ = ["ProjectGroup"]
