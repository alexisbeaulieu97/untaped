"""Root ``untaped project …`` command group: make a folder a project, list the projects.

A project is a folder whose ``.untaped/`` holds ``project.json``
(:mod:`untaped.projects`); the home records each one in ``refs/``, which is
what ``project list`` reads.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from untaped.cli import ColumnsOption, FormatOption, create_app, emit, report_errors, writes
from untaped.management.records import ProjectOutcome, ProjectRow
from untaped.messages import shown_path
from untaped.output.sink import Diagnostics
from untaped.projects import (
    HolderView,
    Project,
    init,
    project_at,
    project_references,
    view,
)
from untaped.refs import Reference
from untaped.stability import experimental

_diag = Diagnostics()


def build_root_project_app() -> App:
    """Return the root ``project`` command group."""
    app = create_app(
        name="project",
        help="Make a folder a project and list the projects this home records.",
        stability=experimental,
    )
    app.command(name="init")(_init_command)
    app.command(name="list")(_list_command)
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
    folder inside another project or holding one.

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
        here = project_at(Path.cwd())
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
