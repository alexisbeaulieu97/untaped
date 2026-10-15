"""Building blocks for ``PluginSpec.migrations`` rows (``untaped setup migrate-dirs``).

:func:`delete_migration` is the whole migration for the commonest case: an
old directory nothing reads any more, deleted with a preview;
:func:`move_migration` is the whole migration for data that only changes
place (into ``plugin_dir``), never merged into or over what is already
there. :func:`setting_guard` keeps a directory a profile still names.
:func:`old_dirs`
finds an older version's directory, custom ones included, and
:func:`dir_bytes` measures what a row moves or frees. :func:`unsafe_dir` says
why a directory must never be moved or deleted whole (it holds home,
untaped's config or the plugins' own data), whatever an old setting named.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from untaped.deprecated_keys import retired_values
from untaped.messages import shown_path, size_text
from untaped.plugins.registry import (
    DirMigration,
    MigrationOptions,
    MigrationOutcome,
    MigrationRow,
    PluginContext,
)
from untaped.settings import (
    DEFAULT_CONFIG_PATH,
    env_var_name,
    resolve_config_path,
    resolve_state_path,
)


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
    ``UNTAPED_STATE``), or overlaps the plugins' own data
    (``~/.untaped/plugins``); a symlink is judged by what it points to.
    """
    real = _real(path)
    untaped = Path(DEFAULT_CONFIG_PATH).expanduser().parent
    if _real(Path.home()).is_relative_to(real):
        return "it holds your home directory"
    kept = (untaped, resolve_config_path().parent, resolve_state_path().parent)
    for held in dict.fromkeys(kept):
        if _real(held).is_relative_to(real):
            return f"it holds {shown_path(held)}"
    if overlapping(path, untaped / "plugins"):
        return f"it overlaps the plugins' data in {shown_path(untaped / 'plugins')}"
    return None


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
        if path.is_symlink():
            target = shown_path(os.path.realpath(path))
            return f"a symlink to {target}: deleting it would leave what it points to"
        return unsafe_dir(path) or (guard(path) if guard is not None else None)

    def preview(_ctx: PluginContext, options: MigrationOptions) -> Sequence[MigrationRow]:
        rows = []
        for path in _existing(paths()):
            reason = refused(path)
            if reason is not None:
                rows.append(MigrationRow(action="keep", source=str(path), detail=f"kept: {reason}"))
                continue
            size = dir_bytes(path) if options.measure else 0
            rows.append(MigrationRow(action="delete", source=str(path), detail=detail, bytes=size))
        return rows

    def apply(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationOutcome]:
        existing = [path for path in _existing(paths()) if refused(path) is None]
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
        """Replace what a command left at ``destination``, then move the group, sidecars first.

        A crash between two moves leaves the source's file in place and its
        moved sidecars at the destination, so the next run moves the rest.
        """
        if _there(destination):
            for path in self.group(destination):
                _remove(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        for old, new in zip(self.group(source), self.group(destination), strict=True):
            if _there(old):
                shutil.move(old, new)


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
