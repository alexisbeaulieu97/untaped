"""Building blocks for ``PluginSpec.migrations`` rows (``untaped setup migrate-dirs``).

:func:`delete_migration` is the whole migration for the commonest case: an
old directory nothing reads any more, deleted with a preview;
:func:`move_migration` is the whole migration for data that only changes
place (into ``plugin_dir``), never merged into or over what is already
there. :func:`setting_guard` keeps a directory a profile still names.
:func:`old_dirs`
finds an older version's directory, custom ones included, and
:func:`dir_bytes` measures what a row moves or frees.
:func:`other_layouts_migration` offers a store's layouts the automatic cleanup
left behind, and :func:`references_migration` the references of installs that
are gone. :func:`unsafe_dir` says
why a directory must never be moved or deleted whole (it holds home,
untaped's config or the plugins' own data), whatever an old setting named.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.deprecated_keys import retired_values
from untaped.messages import shown_path, size_text
from untaped.plugins.registry import (
    DirMigration,
    MigrationOptions,
    MigrationOutcome,
    MigrationRow,
    PluginContext,
    plugins_root,
)
from untaped.refs import refs_dir
from untaped.settings import (
    DEFAULT_CONFIG_PATH,
    env_var_name,
    resolve_config_path,
    resolve_state_path,
)
from untaped.stability import experimental
from untaped.store import Store

if TYPE_CHECKING:
    from untaped.store import Kept, Layout


def dir_bytes(path: Path) -> int:
    """Bytes of every file under ``path`` (a file's own size; symlinks not followed)."""
    try:
        if not path.is_dir() or path.is_symlink():
            return path.lstat().st_size
    except OSError:
        return 0
    total = 0
    for directory, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(directory, name)).st_size
            except OSError:
                continue
    return total


def old_dirs(default: str, section: str, key: str) -> list[Path]:
    """The directory an older version used: ``default``, then each other one configured.

    ``<section>.<key>`` is the deleted setting that could move it, read as
    :func:`configured_paths` reads it. Paths are listed once.
    """
    found = [Path(default).expanduser()]
    found += [path for path in configured_paths(section, key) if path not in found]
    return found


def configured_paths(section: str, key: str) -> list[Path]:
    """Every path ``<section>.<key>`` is set to: config.yml's profiles, then the environment.

    Read with :func:`~untaped.deprecated_keys.retired_values` (every
    profile, as written, so a deleted key is still found until ``config
    migrate`` deletes it) and from its ``UNTAPED_*`` variable (a dotted key
    nests: ``inventory.path`` is ``…__INVENTORY__PATH``). Paths are expanded
    (``~``) and listed once; a relative one is skipped (it would name a
    different directory wherever the command runs).
    """
    env = os.environ.get(env_var_name([section, *key.split(".")]))
    found: list[Path] = []
    for value in (*retired_values(section, key), env):
        if not isinstance(value, str) or not value.strip():
            continue
        path = Path(value).expanduser()
        if path.is_absolute() and path not in found:
            found.append(path)
    return found


def setting_guard(section: str, *keys: str) -> Callable[[Path], str | None]:
    """A ``guard`` keeping a path any profile still sets one of ``<section>.<keys>`` into.

    For :func:`move_migration` and :func:`delete_migration`: a profile that
    names the old place (or a directory inside it) keeps using it, so it
    stays, whatever the active profile says.
    """

    def guard(path: Path) -> str | None:
        for key in keys:
            if any(overlapping(path, used) for used in configured_paths(section, key)):
                return f"config.yml or the environment still sets {section}.{key} there"
        return None

    return guard


def overlapping(left: Path, right: Path) -> bool:
    """Whether one of the two directories is the other or lies inside it (symlinks resolved)."""
    a, b = _real(left), _real(right)
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def unsafe_dir(path: Path) -> str | None:
    """Why ``path`` must never be deleted or moved whole, or ``None``.

    It holds the home directory, untaped's default config directory or the
    directory of the active config or state file (``UNTAPED_CONFIG``,
    ``UNTAPED_STATE``), or overlaps the install references
    (``~/.untaped/refs``) or the plugins' own data (``~/.untaped/plugins``);
    a symlink is judged by what it points to.
    """
    return _unsafe(path, plugins=True)


def _unsafe(path: Path, *, plugins: bool) -> str | None:
    """:func:`unsafe_dir`, with ``plugins=False`` for a store's layout (which lives there)."""
    real = _real(path)
    untaped = Path(DEFAULT_CONFIG_PATH).expanduser().parent
    if _real(Path.home()).is_relative_to(real):
        return "it holds your home directory"
    kept = (untaped, resolve_config_path().parent, resolve_state_path().parent)
    for held in dict.fromkeys(kept):
        if _real(held).is_relative_to(real):
            return f"it holds {shown_path(held)}"
    if overlapping(path, refs_dir()):
        return f"it overlaps the install references in {shown_path(refs_dir())}"
    if plugins and overlapping(path, untaped / "plugins"):
        return f"it overlaps the plugins' data in {shown_path(untaped / 'plugins')}"
    return None


@experimental
def other_layouts_migration(store: Store) -> DirMigration:
    """The ``<plugin>.<store>-layouts`` row: every other layout the cleanup has left.

    One ``delete`` row per layout of ``store`` on disk that no live
    reference holds and that the cleanup will not remove on its own, with
    the reason in its detail (no registered install, a running process has
    it open, a lock this filesystem refuses, a damaged in-use lock); a
    layout the next complete fill removes is not listed. A symlink, a path
    :func:`unsafe_dir` refuses (the plugins' folder aside), one the store's
    ``keep`` names, and every layout while ``refs/`` or a reference cannot
    be read (who holds it is unknown) is a ``keep`` row.
    Applying takes the references' lock, checks again what holds each
    layout, and removes it the way the cleanup does: renamed aside under its
    exclusive lock (a running untaped that has it open fails the row), then
    deleted.
    """
    rows = _OtherLayouts(store)
    title = f"other layouts of the {store.qualified} store"
    return DirMigration(id=rows.id, title=title, preview=rows.preview, apply=rows.apply)


@dataclass(frozen=True)
class _OtherLayouts:
    """What :func:`other_layouts_migration` previews and applies for one store."""

    store: Store

    @property
    def id(self) -> str:
        return f"{self.store.qualified}-layouts"

    def left(self) -> list[tuple[Layout, Kept]]:
        """Each unheld layout the cleanup will not remove on its own, with why."""
        from untaped.refs import read_references  # noqa: PLC0415 - no cycle
        from untaped.store import kept_reason, survey  # noqa: PLC0415 - no cycle

        references = read_references()
        found = []
        for layout in survey(self.store, references):
            if layout.own or layout.holders:
                continue
            reason = kept_reason(self.store, layout, references)
            if reason is not None:
                found.append((layout, reason))
        return found

    def refused(self, path: Path, reason: Kept) -> str | None:
        """Why the row keeps ``path`` rather than offer to delete it, or ``None``."""
        if path.is_symlink():
            return f"a symlink to {shown_path(os.path.realpath(path))}: it stays"
        if reason.code in ("kept", "keep-failed"):
            return reason.text
        if not reason.unheld:
            return f"who holds it is unknown: {reason.text}"
        return _unsafe(path, plugins=False)

    def preview(self, _ctx: PluginContext, options: MigrationOptions) -> Sequence[MigrationRow]:
        rows = []
        for layout, reason in self.left():
            why = self.refused(layout.path, reason)
            if why is not None:
                text = f"kept: {why}"
                rows.append(MigrationRow(action="keep", source=str(layout.path), detail=text))
                continue
            size = dir_bytes(layout.path) if options.measure else 0
            detail = f"layout {layout.layout}, held by nobody: {reason.text}"
            rows.append(
                MigrationRow(action="delete", source=str(layout.path), detail=detail, bytes=size)
            )
        return rows

    def apply(self, _ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationOutcome]:
        from untaped.refs import locked  # noqa: PLC0415 - no cycle
        from untaped.store import _Aside  # noqa: PLC0415 - no cycle

        done: list[_Aside] = []
        problems: list[str] = []
        with locked():  # a `setup register` landing now is seen, or waits
            todo = [
                layout
                for layout, reason in self.left()
                if self.refused(layout.path, reason) is None
            ]
            for layout in todo:
                aside = self.store._set_aside(layout.layout, layout.path, unlocked=True)
                if isinstance(aside, str):
                    problems.append(f"{shown_path(layout.path)} {aside}")
                else:
                    done.append(aside)
        if not todo:
            return [MigrationOutcome(id=self.id, action="unchanged", detail="nothing left")]
        for aside in done:
            problems += aside.delete()
        removed = [f"layout {aside.layout} at {shown_path(aside.path)}" for aside in done]
        if not problems:
            return [MigrationOutcome(id=self.id, action="deleted", detail=_deleted(removed))]
        if not done:
            return [MigrationOutcome(id=self.id, action="failed", detail="; ".join(problems))]
        detail = "; ".join([_deleted(removed), *problems])
        return [MigrationOutcome(id=self.id, action="partial", detail=detail)]


def references_migration() -> DirMigration:
    """Core's ``untaped.references`` row: the reference files of installs that are gone.

    A dead reference counts for nothing, but no process deletes another
    install's registration on its own; this previewed row is where a
    person does. Applying deletes each file that is still dead.
    """
    from untaped.refs import locked, read_references  # noqa: PLC0415 - no cycle

    migration_id = "untaped.references"

    def dead() -> list[Path]:
        return [ref.file for ref in read_references() if ref.opaque is None and not ref.live]

    def preview(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationRow]:
        rows = []
        for ref in read_references():
            if ref.opaque is not None or ref.live:
                continue
            detail = f"{ref.describe()}: no untaped there any more"
            rows.append(MigrationRow(action="delete", source=str(ref.file), detail=detail))
        return rows

    def apply(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationOutcome]:
        with locked():
            files = dead()
            for file in files:
                file.unlink(missing_ok=True)
        if not files:
            return [MigrationOutcome(id=migration_id, action="unchanged", detail="nothing left")]
        return [MigrationOutcome(id=migration_id, action="deleted", detail=_deleted(files))]

    title = "references of installs that are gone"
    return DirMigration(id=migration_id, title=title, preview=preview, apply=apply)


#: The id of core's row for an uninstalled plugin's caches.
UNINSTALLED_CACHES = "untaped.uninstalled-caches"


def uninstalled_caches(installed: Collection[str]) -> list[Path]:
    """Each ``plugins/<name>/cached`` folder whose plugin isn't one of ``installed``, by name."""
    try:
        folders = sorted(plugins_root().iterdir())
    except OSError:
        return []
    return [
        folder / "cached"
        for folder in folders
        if folder.name not in installed and _there(folder / "cached")
    ]


def uninstalled_caches_migration(installed: Collection[str]) -> DirMigration:
    """Core's ``untaped.uninstalled-caches`` row: the caches of plugins not installed here.

    A cache refills, but another install sharing this home may still use
    it, so nothing removes it on its own; this previewed row is where a
    person does. Applying deletes each folder whose plugin is still not
    installed.
    """

    def detail(path: Path) -> str:
        return f"plugin {path.parent.name!r} isn't installed here; another install may use it"

    return _deleting(
        UNINSTALLED_CACHES,
        "caches of plugins not installed here",
        lambda: uninstalled_caches(installed),
        detail=detail,
        refused=lambda path: _unsafe(path, plugins=False),
    )


def _deleted(items: Sequence[object]) -> str:
    return "deleted " + ", ".join(
        shown_path(item) if isinstance(item, Path) else str(item) for item in items
    )


def delete_migration(
    id: str,
    title: str,
    paths: Callable[[], Sequence[Path]],
    *,
    detail: str = "",
    guard: Callable[[Path], str | None] | None = None,
) -> DirMigration:
    """A migration that deletes ``paths()`` (directories or files) when they exist.

    The preview has one ``delete`` row per existing path, with its size and
    ``detail``; applying deletes them and reports ``deleted`` (or
    ``unchanged`` when none is left; ``partial`` when a later path fails
    after an earlier one was deleted). A symlink, a path :func:`unsafe_dir`
    refuses, or one ``guard`` returns a reason for (a directory another
    plugin still uses), is a ``keep`` row with that reason instead, and stays. ``paths``
    runs at preview and at apply time, so it may read settings.
    """

    def refused(path: Path) -> str | None:
        return unsafe_dir(path) or (guard(path) if guard is not None else None)

    return _deleting(id, title, paths, detail=lambda _path: detail, refused=refused)


def _deleting(
    id: str,
    title: str,
    paths: Callable[[], Sequence[Path]],
    *,
    detail: Callable[[Path], str],
    refused: Callable[[Path], str | None],
) -> DirMigration:
    """:func:`delete_migration` with each path's own detail and refusal (a symlink always stays)."""

    def kept(path: Path) -> str | None:
        if path.is_symlink():
            target = shown_path(os.path.realpath(path))
            return f"a symlink to {target}: deleting it would leave what it points to"
        return refused(path)

    def preview(_ctx: PluginContext, options: MigrationOptions) -> Sequence[MigrationRow]:
        rows = []
        for path in _existing(paths()):
            reason = kept(path)
            if reason is not None:
                rows.append(MigrationRow(action="keep", source=str(path), detail=f"kept: {reason}"))
                continue
            size = dir_bytes(path) if options.measure else 0
            rows.append(
                MigrationRow(action="delete", source=str(path), detail=detail(path), bytes=size)
            )
        return rows

    def apply(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationOutcome]:
        existing = [path for path in _existing(paths()) if kept(path) is None]
        if not existing:
            return [MigrationOutcome(id=id, action="unchanged", detail="nothing left to delete")]
        freed = 0
        done: list[str] = []
        for path in existing:
            size = dir_bytes(path)
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError as exc:
                text = f"could not delete {shown_path(path)}: {exc.strerror or exc}"
                if not done:
                    return [MigrationOutcome(id=id, action="failed", detail=text)]
                text = f"deleted {', '.join(done)} ({size_text(freed)} freed); {text}"
                return [MigrationOutcome(id=id, action="partial", detail=text)]
            freed += size
            done.append(shown_path(path))
        text = f"deleted {', '.join(done)} ({size_text(freed)} freed)"
        return [MigrationOutcome(id=id, action="deleted", detail=text)]

    return DirMigration(id=id, title=title, preview=preview, apply=apply)


def move_migration(
    id: str,
    title: str,
    moves: Callable[[], Sequence[tuple[Path, Path]]],
    *,
    detail: str = "",
    sidecars: Sequence[str] = (),
    guard: Callable[[Path], str | None] | None = None,
    replaceable: Callable[[Path], bool] | None = None,
) -> DirMigration:
    """A migration that moves each ``(source, destination)`` of ``moves()`` whose source exists.

    The preview has one ``move`` row per existing source, with its size and
    ``detail``; applying renames it into place (creating the destination's
    parent, copying across filesystems) and reports ``moved``, or
    ``unchanged`` when nothing is left to move (``partial`` when a later pair
    fails after an earlier one moved). Each suffix in ``sidecars`` names a
    file that travels with its pair's source (``-journal`` beside a SQLite
    file): moved first, and only with it. Nothing is merged: a destination
    that already exists keeps the source where it is, as a ``keep`` row,
    unless it is an empty directory or ``replaceable(destination)`` says it
    holds only what a command regenerates (an index nothing was saved to,
    created by a command run before the migration); then it, and its
    sidecars, are replaced. A source that is a symlink, that
    :func:`unsafe_dir` refuses or that ``guard`` returns a reason for (see
    :func:`setting_guard`) is a ``keep`` row too. ``moves`` runs at preview
    and at apply time, so it may read settings.
    """

    move = _Move(moves, sidecars=tuple(sidecars), guard=guard, replaceable=replaceable)

    def preview(_ctx: PluginContext, options: MigrationOptions) -> Sequence[MigrationRow]:
        return [
            move.row(source, destination, detail, options) for source, destination in move.pending()
        ]

    def apply(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationOutcome]:
        todo = [pair for pair in move.pending() if move.refused(*pair) is None]
        if not todo:
            return [MigrationOutcome(id=id, action="unchanged", detail="nothing left to move")]
        done: list[str] = []
        for source, destination in todo:
            try:
                move.apply(source, destination)
            except OSError as exc:
                text = f"could not move {shown_path(source)}: {exc.strerror or exc}"
                if not done:
                    return [MigrationOutcome(id=id, action="failed", detail=text)]
                text = f"moved {', '.join(done)}; {text}"
                return [MigrationOutcome(id=id, action="partial", detail=text)]
            done.append(f"{shown_path(source)} to {shown_path(destination)}")
        return [MigrationOutcome(id=id, action="moved", detail=f"moved {', '.join(done)}")]

    return DirMigration(id=id, title=title, preview=preview, apply=apply)


@dataclass(frozen=True)
class _Move:
    """What :func:`move_migration` decides per ``(source, destination)`` pair."""

    moves: Callable[[], Sequence[tuple[Path, Path]]]
    sidecars: tuple[str, ...]
    guard: Callable[[Path], str | None] | None
    replaceable: Callable[[Path], bool] | None

    def pending(self) -> list[tuple[Path, Path]]:
        return [(source, dest) for source, dest in dict.fromkeys(self.moves()) if _there(source)]

    def group(self, path: Path) -> list[Path]:
        """``path`` and its sidecars, sidecars first (the order they move in)."""
        return [*(path.with_name(path.name + end) for end in self.sidecars), path]

    def replaced(self, destination: Path) -> bool:
        if destination.is_symlink():
            return False
        if _empty_dir(destination):
            return True
        return self.replaceable is not None and self.replaceable(destination)

    def refused(self, source: Path, destination: Path) -> str | None:
        """Why ``source`` stays where it is, or ``None``."""
        if source.is_symlink():
            target = shown_path(os.path.realpath(source))
            return f"a symlink to {target}: move what it points to, or point the setting at it"
        reason = self.guard(source) if self.guard is not None else None
        if reason is None and _there(destination) and not self.replaced(destination):
            reason = f"{shown_path(destination)} already exists; keep one of them"
        if reason is None and source.is_dir():
            reason = unsafe_dir(source)
        return reason

    def row(
        self, source: Path, destination: Path, detail: str, options: MigrationOptions
    ) -> MigrationRow:
        reason = self.refused(source, destination)
        if reason is not None:
            return MigrationRow(action="keep", source=str(source), detail=f"kept: {reason}")
        if _there(destination) and not _empty_dir(destination):
            replacing = f"replaces the {shown_path(destination)} a command made before the move"
            detail = f"{detail}; {replacing}" if detail else replacing
        size = sum(dir_bytes(path) for path in self.group(source) if _there(path))
        return MigrationRow(
            action="move",
            source=str(source),
            destination=str(destination),
            detail=detail,
            bytes=size if options.measure else 0,
        )

    def apply(self, source: Path, destination: Path) -> None:
        """Replace what a command left at ``destination``, then move the group, sidecars first."""
        if _there(destination):
            for path in self.group(destination):
                _remove(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        move_group(source, destination, self.sidecars)


def move_group(
    source: Path,
    destination: Path,
    sidecars: Sequence[str] = (),
    *,
    rename: Callable[[Path, Path], object] = shutil.move,
) -> None:
    """Move ``source`` to ``destination`` with its sidecars, sidecars first.

    Each suffix in ``sidecars`` names a file beside ``source`` that travels
    with it (``-journal`` beside a SQLite file), moved only when it exists.
    ``rename`` moves one path: :func:`shutil.move` copies across
    filesystems; :func:`os.rename` never copies, so a file another process
    has open is either renamed whole or raises. A crash between two moves
    leaves the source's file in place and its moved sidecars at the
    destination, so the next run moves the rest.
    """
    for end in (*sidecars, ""):
        old = source.with_name(source.name + end)
        if _there(old):
            rename(old, destination.with_name(destination.name + end))


def _remove(path: Path) -> None:
    """Delete a file or a whole directory (not what a symlink points to); nothing there is fine."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _empty_dir(path: Path) -> bool:
    """Whether ``path`` is a directory (not a symlink) with nothing in it."""
    try:
        return not path.is_symlink() and path.is_dir() and not any(path.iterdir())
    except OSError:
        return False


def _there(path: Path) -> bool:
    """Whether anything is at ``path``, a dangling symlink included."""
    return path.exists() or path.is_symlink()


def _existing(paths: Sequence[Path]) -> list[Path]:
    """The paths that exist, once each."""
    seen: list[Path] = []
    for path in paths:
        if _there(path) and path not in seen:
            seen.append(path)
    return seen


def _real(path: Path) -> Path:
    return Path(os.path.realpath(path.expanduser()))
