"""Root ``untaped project …`` command group: make, list, move, remove and prune projects.

A project is a folder whose ``.untaped/`` holds ``project.json``
(:mod:`untaped.projects`); the home records each one in ``refs/``, which is
what ``project list`` reads. ``remove`` and ``prune`` release a project's
holds through their owners (:mod:`untaped.project_lifecycle`), previewed
and confirmed; ``move`` moves a folder and has each owner repair it.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from cyclopts import App, Parameter

from untaped.batch import finish
from untaped.cli import (
    ColumnsOption,
    DryRunOption,
    FormatOption,
    YesOption,
    create_app,
    emit,
    render_rows,
    report_errors,
    writes,
)
from untaped.errors import ConfigError, UntapedError, UsageError
from untaped.holds import HolderView, HoldOutcome, unnamed, view
from untaped.management.records import ProjectOutcome, ProjectRow
from untaped.messages import plural, shown_path
from untaped.output.sink import Diagnostics
from untaped.projects import (
    Project,
    current_project,
    init,
    project_at,
    project_references,
    writing,
)

if TYPE_CHECKING:
    from untaped.project_lifecycle import Removal
from untaped.refs import Reference
from untaped.stability import experimental
from untaped.theme import OutputFormat
from untaped.ui import ui_context

_diag = Diagnostics()

ForceOption = Annotated[
    list[str] | None,
    Parameter(
        name="--force",
        negative="",
        consume_multiple=False,
        help="Override the refusal for this item (repeatable): it is released anyway, "
        "or, when its plugin isn't installed, the hold is forgotten.",
    ),
]
CheckOption = Annotated[
    bool,
    Parameter(name="--check", negative="", help="Preview, and exit 3 when a hold is refused."),
]
ProjectArgument = Annotated[
    str | None,
    Parameter(help="The project: its folder, or its name; the current project when left out."),
]
#: The columns ``prune`` shows: it acts on several projects at once.
_PRUNE_COLUMNS = ["holder", *HoldOutcome.table_columns]


def build_root_project_app() -> App:
    """Return the root ``project`` command group."""
    app = create_app(
        name="project",
        help="Make a folder a project and list the projects this home records.",
        stability=experimental,
    )
    app.command(name="init")(_init_command)
    app.command(name="list")(_list_command)
    app.command(name="move")(_move_command)
    app.command(name="remove")(_remove_command)
    app.command(name="prune")(_prune_command)
    return app


@writes
def _init_command(
    path: Annotated[
        Path | None,
        Parameter(help="The folder to make a project; the current folder when left out."),
    ] = None,
    /,
    *,
    new_id: Annotated[
        bool,
        Parameter(
            name="--new-id",
            negative="",
            help="Give a copied project its own id (never the folder its record names).",
        ),
    ] = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Make a folder a project: its .untaped/ holds project.json, and this home records it.

    Creates the folder when it doesn't exist. Run again in a project, it
    brings the project's record up to date: records it in this home, follows
    a move, or gives a copy its own id. Refuses ~ and ~/.untaped, and a
    folder inside another project or holding a recorded one.

    Examples:
        $ untaped project init
        $ untaped project init ~/work/PROJ-123
    """
    with report_errors():
        done = init(path if path is not None else Path.cwd(), new_id=new_id)
        if done.action == "unchanged":
            _diag.info(done.detail)
        else:
            _diag.success(done.detail)
        emit(
            [
                ProjectOutcome(
                    name=done.project.name,
                    path=str(done.project.root),
                    id=done.project.id,
                    action=done.action,
                    detail=done.detail,
                )
            ],
            fmt=fmt,
            columns=columns,
        )


def _list_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """List the projects this home records, with each one's state.

    A project is present, unreadable (its drive or share isn't there, so it
    keeps what it holds) or missing (its folder is gone). current marks the
    one the working directory is in.

    Examples:
        $ untaped project list
        $ untaped project list -f json
    """
    with report_errors():
        try:
            here = project_at(Path.cwd())
        except ConfigError as exc:  # a damaged marker here: still list the others
            _diag.warning(str(exc), hint=exc.hint)
            here = None
        rows = [_row(ref, here) for ref in project_references()]
        emit(
            rows,
            fmt=fmt,
            columns=columns,
            empty="No projects yet. Make one with `untaped project init` in its folder.",
        )


def _row(ref: Reference, here: Project | None) -> ProjectRow:
    seen = view(ref)
    if not isinstance(seen, HolderView):
        return ProjectRow(
            name="", path=str(ref.file), state="unknown", current=False, detail=seen.label
        )
    holds: dict[str, list[str]] = {}
    for target in ref.targets:
        if target.item is not None:
            holds.setdefault(f"{target.plugin}.{target.store}", []).append(target.item)
    return ProjectRow(
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


__all__ = ["build_root_project_app"]


@writes
def _move_command(
    old: Annotated[Path, Parameter(help="Where the project's folder is, or was.")],
    new: Annotated[Path, Parameter(help="Where it goes, or where it already is.")],
    /,
    *,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
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
    from untaped.project_lifecycle import apply_move, plan_move  # noqa: PLC0415 - runs only here

    with report_errors():
        move = plan_move(old, new)
        if dry_run:
            emit(move.preview(), fmt=fmt, columns=columns)
            return
        if move.how == "adopt":
            ui_context(strict=False).confirm_or_cancel(
                f"Make {shown_path(move.new)} project {move.project.name} "
                f"(its record names {shown_path(move.old)})?",
                assume_yes=yes,
                refusal="project move requires --yes when not interactive",
            )
        rows = apply_move(move)
        emit(rows, fmt=fmt, columns=columns)
        _report_failures(rows)
    finish(any(row.failed for row in rows))


@writes(destructive=True)
def _remove_command(
    project: ProjectArgument = None,
    /,
    *,
    force: ForceOption = None,
    check: CheckOption = False,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Remove a project: release what it holds, then delete what untaped wrote in it.

    Each plugin the project holds items of says how it lets go (a repo
    another project holds is kept), shown first. One refused hold applies
    nothing (exit 1); --force ITEM overrides that item's refusal. Then
    project.json, state.yml, plugins/ and its own lock files go; config.yml and
    your own files stay, and so does the folder unless nothing else is in
    it. Always confirms; without a terminal it needs --yes (else exit 2).

    Examples:
        $ untaped project remove --dry-run
        $ untaped project remove ~/work/PROJ-123 --force github.com/acme/web
    """
    from untaped.project_lifecycle import (  # noqa: PLC0415 - runs only here
        find_project,
        plan_removal,
        preview_check,
        unlink_lock,
    )

    with report_errors():
        found = current_project() if project is None else find_project(project)
        preview = dry_run or check
        with ExitStack() as stack:
            if isinstance(found, Project):
                stack.enter_context(found.locked())
                if preview:
                    preview_check(found)
                else:
                    found = writing(found)[0]
            elif found.holder is not None:
                stack.enter_context(
                    Project(id=found.holder.name, root=found.anchor or Path()).locked()
                )
            removal = plan_removal(found, force=force or ())
            rows = _run(
                [removal],
                force=force or (),
                preview=preview,
                check=check,
                yes=yes,
                fmt=fmt,
                columns=columns,
                question=f"Remove project {removal.name}?",
                verb="removed",
            )
        unlink_lock(removal.holder)
    finish(any(row.action in {"failed", "refused"} for row in rows))


@writes(destructive=True)
def _prune_command(
    project: Annotated[
        str | None,
        Parameter(
            help="One missing project, by its folder or name; every missing one when left out."
        ),
    ] = None,
    /,
    *,
    force: ForceOption = None,
    check: CheckOption = False,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Remove the projects whose folder is gone, releasing what they hold.

    A project whose folder can't be read now (a drive or share that isn't
    there) is listed and skipped: it keeps what it holds. Previewed and
    confirmed as `project remove`; one refused hold applies nothing.

    Examples:
        $ untaped project prune --dry-run
        $ untaped project prune --yes
    """
    from untaped.project_lifecycle import (  # noqa: PLC0415 - runs only here
        find_project,
        missing_projects,
        plan_removal,
        unlink_lock,
    )

    with report_errors():
        if project is None:
            missing, unreadable = missing_projects()
            for ref in unreadable:
                where = shown_path(ref.anchor) if ref.anchor is not None else str(ref.file)
                _diag.info(f"skipped {where}: it can't be read now, so it keeps what it holds")
        else:
            named = find_project(project)
            if isinstance(named, Project):
                raise UntapedError(
                    f"project {named.name} is at {shown_path(named.root)}; prune removes only "
                    "projects whose folder is gone",
                    hint="remove it with `untaped project remove`",
                )
            missing = [named]
        if not missing:
            _diag.info("no project's folder is gone; nothing to prune")
            emit([], fmt=fmt, columns=columns or _PRUNE_COLUMNS)
            return
        preview = dry_run or check
        with ExitStack() as stack:
            removals = []
            for ref in missing:
                if ref.holder is None:  # missing_projects lists only readable records
                    continue
                stack.enter_context(Project(id=ref.holder.name, root=ref.anchor or Path()).locked())
                try:
                    removals.append(plan_removal(ref, force=force or ()))
                except UntapedError as exc:  # back, or out of reach, since it was listed
                    if project is not None:
                        raise
                    _diag.info(f"skipped {ref.describe()}: {exc}")
            if not removals:
                _diag.info("nothing to prune")
                emit([], fmt=fmt, columns=columns or _PRUNE_COLUMNS)
                return
            rows = _run(
                removals,
                force=force or (),
                preview=preview,
                check=check,
                yes=yes,
                fmt=fmt,
                columns=columns or _PRUNE_COLUMNS,
                question=f"Prune {plural(len(removals), 'project')}?",
                verb="pruned",
            )
        for removal in removals:
            unlink_lock(removal.holder)
    finish(any(row.action in {"failed", "refused"} for row in rows))


def _run(
    removals: Sequence[Removal],
    *,
    force: Sequence[str],
    preview: bool,
    check: bool,
    yes: bool,
    fmt: OutputFormat,
    columns: list[str] | None,
    question: str,
    verb: str,
) -> list[HoldOutcome]:
    """The service's steps over the planned removals: preview, refuse, confirm, apply."""
    from untaped.project_lifecycle import apply_removal, blocked_hint  # noqa: PLC0415

    plans = [plan for removal in removals for plan in removal.plans]
    unknown = unnamed(force, plans)
    if unknown:
        raise UsageError(
            f"--force names {', '.join(unknown)}, which "
            f"{'is' if len(unknown) == 1 else 'are'} not held here",
            hint="name items as the rows show them",
        )
    planned = [row for removal in removals for row in removal.preview()]
    blocked = [plan for plan in plans if plan.blocked]
    if preview:
        emit(planned, fmt=fmt, columns=columns)
        finish(False, predicate_hit=check and bool(blocked))
        return []
    if blocked:
        emit(planned, fmt=fmt, columns=columns)
        raise UntapedError(
            f"{plural(len(blocked), 'hold')} refused; nothing {verb}",
            hint=blocked_hint(blocked),
        )
    ui_context(strict=False).confirm_or_cancel(
        question,
        assume_yes=yes,
        refusal=f"project {verb.removesuffix('d')} requires --yes when not interactive",
        preview=lambda: _diag.info(
            render_rows(
                [row.model_dump() for row in planned],
                fmt="table",
                table_columns=columns or HoldOutcome.table_columns,
            ),
            unmuted=True,
        ),
    )
    rows = [row for removal in removals for row in apply_removal(removal)]
    emit(rows, fmt=fmt, columns=columns)
    _report_failures(rows)
    return rows


def _report_failures(rows: Sequence[HoldOutcome]) -> None:
    for row in rows:
        if row.error is not None:
            _diag.failure(row.error, item=f"{row.plugin} {row.item}")
        elif row.action == "refused":
            _diag.warning(f"{row.plugin} {row.item}: {row.detail}", hint=row.hint)
