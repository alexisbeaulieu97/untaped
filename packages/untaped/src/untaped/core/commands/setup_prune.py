"""``untaped setup prune``: remove what unreachable installs and uninstalled plugins left.

Two kinds of leftovers, previewed together and removed after one
confirmation. The reference of an install that is not reachable (its folder
no longer holds untaped) counts for nothing, but no run deletes another
install's registration on its own (``setup register`` and the runs that
refresh a reference never drop one: an unmounted disk looks the same). The
``plugins/<name>/`` folder of a plugin not installed here holds that
plugin's data, caches and state. A folder counts when its name could be a
plugin's, it is not core's own and no plugin is composed or quarantined
under that name; it stays, as a ``kept`` row, when it is a symlink, when a
live reference names its plugin (an install composing it, or a target of
it), or while any reference cannot be read or a live install's doesn't
list the plugins it composes. Applying holds the references'
lock throughout, checks each item again and removes nothing outside the
home's ``plugins/``; an item that fails is a failed row and the others
still go.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.core.commands.records import PruneOutcome
from untaped.core.settings import CoreSettings
from untaped.runtime.settings import PLUGIN_NAME_PATTERN, plugins_root
from untaped.sdk.errors import UntapedError
from untaped.sdk.fields import Confirm, Output, key, table_row
from untaped.sdk.plugin import Command
from untaped.sdk.storage._fs import dir_bytes
from untaped.sdk.words import plural, shown_path, size_text

if TYPE_CHECKING:
    from untaped.runtime.registry import CompositionResult
    from untaped.sdk._run import Diag
    from untaped.sdk.storage._refs import Reference


@dataclass(frozen=True)
class Leftover:
    """A ``plugins/<name>/`` folder of a plugin not installed here, and why it stays, if it does."""

    path: Path
    kept: str | None


def leftover_folders(result: CompositionResult, references: Sequence[Reference]) -> list[Leftover]:
    """Each ``plugins/<name>/`` folder whose plugin is not installed here, by name.

    Installed is every plugin composed, core included, or quarantined (its
    data is still its own). A name no plugin could have is never listed.
    """
    installed = {
        *(plugin.name for plugin in result.plugins),
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
    """The references of installs that are not reachable: their folder holds no untaped."""
    return [ref for ref in references if ref.opaque is None and not ref.live]


def _kept(folder: Path, references: Sequence[Reference]) -> str | None:
    """Why ``folder`` stays, or ``None`` when it may go."""
    if folder.is_symlink():
        return f"a symlink to {shown_path(os.path.realpath(folder))}: it stays"
    if Path(os.path.realpath(folder)) != Path(os.path.realpath(plugins_root())) / folder.name:
        return f"not in {shown_path(plugins_root())}: it stays"
    unknown = next((ref for ref in references if ref.opaque is not None), None)
    if unknown is not None:
        return (
            f"another install sharing this home may use it: {shown_path(unknown.file)} "
            f"is {unknown.opaque}"
        )
    user = next((ref for ref in references if _names(ref, folder.name)), None)
    if user is not None:
        return f"{user.describe()} uses it"
    silent = next((ref for ref in references if _unlisted(ref)), None)
    if silent is not None:
        return (
            f"{silent.describe()} may use it: {shown_path(silent.file)} doesn't list "
            "the plugins it composes; upgrade that install, or delete that file if "
            "the install is gone"
        )
    return None


def _names(ref: Reference, plugin: str) -> bool:
    """Whether live ``ref`` names ``plugin``: among the plugins it composes, or a target's."""
    if not ref.live:
        return False
    composed = ref.plugins or ()
    return plugin in composed or any(target.plugin == plugin for target in ref.targets)


def _unlisted(ref: Reference) -> bool:
    """Whether ``ref`` is a live install's that doesn't say which plugins it composes."""
    from untaped.sdk.storage._refs import INSTALL  # noqa: PLC0415 - read only with references

    return ref.kind == INSTALL and ref.plugins is None and ref.live


class Prune(Command[CoreSettings]):
    """Remove unreachable installs' references and the folders of plugins nothing here uses.

    An install deleted without `setup register --remove` leaves its
    reference, which is not reachable and counts for nothing. A plugin
    uninstalled here leaves its folder in plugins/, with its data, caches
    and state; the folder goes whole once no live reference names the
    plugin (an install sharing this home that composes it, a project
    holding its items). Both are listed first; a symlinked folder, one a
    live reference names, and every folder while a reference can't be read
    or a live install's doesn't list its plugins stay. Always confirms;
    without a terminal it needs --yes (else exit 2).
    An item that can't be removed fails the run, and the others still go;
    a busy references lock fails them all (exit 5, retry later).

    Examples:
        $ untaped setup prune --dry-run
        $ untaped setup prune --yes
    """

    repairs = True
    confirm: Confirm
    output: Output[PruneOutcome] = Output(
        columns=(
            key(PruneOutcome).what,
            key(PruneOutcome).target_path,
            key(PruneOutcome).action,
            key(PruneOutcome).detail,
        )
    )

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
            render_rows([table_row(row) for row in rows], fmt="table", table_columns=columns)
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
        detail=f"{ref.describe()}: not reachable",
        bytes=_size(ref.file),
    )


def _folder_row(leftover: Leftover) -> PruneOutcome:
    path = leftover.path
    size = _size(path)
    detail = (
        f"plugin {path.name!r} isn't installed here and no install sharing this home uses it: "
        f"the whole folder goes, its state included ({size_text(size)})"
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
    """Every row applied, under the references' lock: the references, then the folders.

    The lock keeps a reference from being written between a folder's check
    and its removal; when it can't be held, every row that would go fails.
    """
    from untaped.sdk.storage._refs import locked, read_references  # noqa: PLC0415

    try:
        with locked():
            done = [_reference_removed(row, diag) for row in rows if row.what == "reference"]
            # read again: a reference written since the preview may name a folder's plugin
            now = read_references()
            return [
                *done,
                *(_folder_removed(row, now, diag) for row in rows if row.what == "folder"),
            ]
    except UntapedError as exc:  # the lock is busy, or its folder can't be made
        return [row if row.action != "planned" else _failed(row, exc, diag) for row in rows]


def _reference_removed(row: PruneOutcome, diag: Diag) -> PruneOutcome:
    from untaped.sdk.storage._refs import read_reference  # noqa: PLC0415 - runs only here

    file = row.target_path
    if not file.is_file():
        return row.model_copy(update={"action": "skipped", "detail": "already removed"})
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
        return row.model_copy(update={"action": "skipped", "detail": "already removed"})
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
