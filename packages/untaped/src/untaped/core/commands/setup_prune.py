"""``untaped setup prune``: remove what installs and plugins that are gone left in the home.

Two kinds of leftovers, previewed together and removed after one
confirmation. The reference of an install whose folder no longer holds
untaped counts for nothing, but no run deletes another install's
registration on its own (``setup register`` and the runs that refresh a
reference never drop one: an unmounted disk looks the same). The
``plugins/<name>/`` folder of a plugin not installed here holds that
plugin's data, caches and state, which another install sharing the home
may still use. A folder counts when its name could be a plugin's, it is not
core's own and no plugin is composed or quarantined under that name; it
stays, as a ``kept`` row, when it is a symlink or a reference still names
its plugin's data. Applying checks each item again (the references under
their lock) and removes nothing outside the home's ``plugins/``; an item
that fails is a failed row and the others still go.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.core.commands.records import PruneOutcome
from untaped.core.settings import NAME, CoreSettings
from untaped.runtime.settings import PLUGIN_NAME_PATTERN, plugins_root
from untaped.sdk.errors import UntapedError
from untaped.sdk.fields import Confirm, Output
from untaped.sdk.plugin import Command
from untaped.sdk.storage._fs import dir_bytes
from untaped.sdk.words import plural, shown_path, size_text

if TYPE_CHECKING:
    from untaped.runtime.registry import CompositionResult
    from untaped.sdk.run import Diag
    from untaped.sdk.storage._refs import Reference


@dataclass(frozen=True)
class Leftover:
    """A ``plugins/<name>/`` folder of a plugin not installed here, and why it stays, if it does."""

    path: Path
    kept: str | None


def leftover_folders(result: CompositionResult, references: Sequence[Reference]) -> list[Leftover]:
    """Each ``plugins/<name>/`` folder whose plugin is not installed here, by name.

    Installed is core and every plugin composed or quarantined (its data is
    still its own). A name no plugin could have is never listed.
    """
    installed = {
        NAME,
        *(registered.spec.name for registered in result.plugins),
        *(record.name for record in result.quarantine),
    }
    try:
        folders = sorted(plugins_root().iterdir())
    except OSError:
        return []
    return [
        Leftover(folder, _kept(folder, references))
        for folder in folders
        if PLUGIN_NAME_PATTERN.fullmatch(folder.name) and folder.name not in installed
        if folder.is_dir() or folder.is_symlink()
    ]


def dead_references(references: Sequence[Reference]) -> list[Reference]:
    """The references of installs whose folder no longer holds untaped."""
    return [ref for ref in references if ref.opaque is None and not ref.live]


def _kept(folder: Path, references: Sequence[Reference]) -> str | None:
    """Why ``folder`` stays, or ``None`` when it may go."""
    if folder.is_symlink():
        return f"a symlink to {shown_path(os.path.realpath(folder))}: it stays"
    if Path(os.path.realpath(folder)) != Path(os.path.realpath(plugins_root())) / folder.name:
        return f"not in {shown_path(plugins_root())}: it stays"
    unknown = next((ref for ref in references if ref.opaque is not None), None)
    if unknown is not None:
        return f"who uses it is unknown: {shown_path(unknown.file)} is {unknown.opaque}"
    holder = next(
        (
            ref
            for ref in references
            if ref.live and any(target.plugin == folder.name for target in ref.targets)
        ),
        None,
    )
    if holder is not None:
        return f"{holder.describe()} holds its data"
    return None


class Prune(Command[CoreSettings]):
    """Remove the references of installs that are gone and the folders of plugins not here.

    An install deleted without `setup register --remove` leaves its
    reference, which counts for nothing. A plugin uninstalled here leaves
    its folder in plugins/, with its data, caches and state, which another
    install sharing this home may still use. Both are listed first; a
    symlinked folder, or one whose plugin's data a reference still names,
    stays. Always confirms; without a terminal it needs --yes (else exit 2).
    An item that can't be removed fails (exit 1), and the others still go.

    Examples:
        $ untaped setup prune --dry-run
        $ untaped setup prune --yes
    """

    repairs = True
    confirm: Confirm
    output: Output[PruneOutcome]

    def run(self) -> Iterator[PruneOutcome]:
        # Lazy: bootstrap is the CLI's root and imports this module.
        from untaped.runtime.bootstrap import composition  # noqa: PLC0415
        from untaped.sdk._cli import render_rows  # noqa: PLC0415 - rendering is heavy
        from untaped.sdk.storage._refs import read_references  # noqa: PLC0415

        references = read_references()
        rows = [
            *(_reference_row(ref) for ref in dead_references(references)),
            *(_folder_row(each) for each in leftover_folders(composition(), references)),
        ]
        planned = [row for row in rows if row.action == "planned"]
        if not planned:
            self.ctx.diag.info("nothing to prune")
            yield from rows
            return
        columns = list(self.output.columns)
        # the planned rows are what --dry-run prints, so only a question shows them first
        preview = lambda: [  # noqa: E731
            render_rows([row.model_dump() for row in rows], fmt="table", table_columns=columns)
        ]
        question = f"Remove {_counted(planned)}?"
        if not self.confirm(question, preview=None if self.confirm.dry_run else preview):
            yield from rows
            return
        done = _removed(rows, self.ctx.diag)
        yield from done
        removed = [row for row in done if row.action == "removed"]
        if removed:
            self.ctx.diag.success(f"removed {_counted(removed)}")


def _reference_row(ref: Reference) -> PruneOutcome:
    return PruneOutcome(
        what="reference",
        target_path=ref.file,
        action="planned",
        detail=f"{ref.describe()}: no untaped there any more",
        bytes=_size(ref.file),
    )


def _folder_row(leftover: Leftover) -> PruneOutcome:
    path = leftover.path
    size = _size(path)
    detail = (
        f"plugin {path.name!r} isn't installed here ({size_text(size)}); "
        "another install sharing this home may use it"
    )
    return PruneOutcome(
        what="folder",
        target_path=path,
        action="planned" if leftover.kept is None else "kept",
        detail=detail if leftover.kept is None else leftover.kept,
        bytes=size,
    )


def _size(path: Path) -> int:
    return 0 if path.is_symlink() else dir_bytes(path)


def _counted(rows: Sequence[PruneOutcome]) -> str:
    """``2 references and 1 plugin folder``: what ``rows`` are about, counted."""
    references = sum(row.what == "reference" for row in rows)
    folders = len(rows) - references
    parts = [
        *([plural(references, "reference")] if references else []),
        *([plural(folders, "plugin folder")] if folders else []),
    ]
    return " and ".join(parts)


def _removed(rows: Sequence[PruneOutcome], diag: Diag) -> list[PruneOutcome]:
    """Every row applied: the references under their lock first, then the folders."""
    from untaped.sdk.storage._refs import locked, read_references  # noqa: PLC0415

    references = [row for row in rows if row.what == "reference"]
    done: list[PruneOutcome] = []
    if references:
        try:
            with locked():
                done = [_reference_removed(row, diag) for row in references]
        except UntapedError as exc:  # the lock is busy, or its folder can't be made
            done = [_failed(row, exc, diag) for row in references]
    # read again: a reference written since the preview may name a folder's plugin
    now = read_references()
    return [*done, *(_folder_removed(row, now, diag) for row in rows if row.what == "folder")]


def _reference_removed(row: PruneOutcome, diag: Diag) -> PruneOutcome:
    from untaped.sdk.storage._refs import read_reference  # noqa: PLC0415 - runs only here

    file = row.target_path
    if not file.is_file():
        return row.model_copy(update={"action": "skipped", "detail": "already gone"})
    found = read_reference(file)
    if found.opaque is not None or found.live:
        detail = f"{found.describe()}: written again since the preview, so it stays"
        return row.model_copy(update={"action": "skipped", "detail": detail})
    try:
        file.unlink()
    except OSError as exc:
        error = UntapedError(f"could not remove {shown_path(file)}: {exc.strerror or exc}")
        return _failed(row, error, diag)
    return row.model_copy(update={"action": "removed"})


def _folder_removed(row: PruneOutcome, references: Sequence[Reference], diag: Diag) -> PruneOutcome:
    if row.action != "planned":
        return row
    path = row.target_path
    if not (path.exists() or path.is_symlink()):
        return row.model_copy(update={"action": "skipped", "detail": "already gone"})
    kept = _kept(path, references)
    if kept is not None:
        return row.model_copy(update={"action": "kept", "detail": kept})
    try:
        shutil.rmtree(path)
    except OSError as exc:
        error = UntapedError(f"could not remove {shown_path(path)}: {exc.strerror or exc}")
        return _failed(row, error, diag)
    return row.model_copy(update={"action": "removed"})


def _failed(row: PruneOutcome, error: UntapedError, diag: Diag) -> PruneOutcome:
    failure = diag.failure(error, item=shown_path(row.target_path))
    return row.model_copy(update={"action": "failed", "detail": str(error), "error": failure})


__all__ = ["Leftover", "Prune", "dead_references", "leftover_folders"]
