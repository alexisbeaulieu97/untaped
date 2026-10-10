"""Building blocks for ``PluginSpec.migrations`` rows (``untaped setup migrate-dirs``).

:func:`delete_migration` is the whole migration for the commonest case: an
old directory nothing reads any more, deleted with a preview. :func:`old_dirs`
finds an older version's directory, custom ones included, and
:func:`dir_bytes` measures what a row moves or frees. :func:`unsafe_dir` says
why a directory must never be moved or deleted whole (it holds home,
untaped's config or the plugins' own data), whatever an old setting named.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Sequence
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
from untaped.settings import DEFAULT_CONFIG_PATH


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

    ``<section>.<key>`` is the deleted setting that could move it, read from
    config.yml with :func:`~untaped.deprecated_keys.retired_values` (every
    profile) and from its ``UNTAPED_<SECTION>__<KEY>`` environment variable.
    Paths are expanded (``~``) and listed once; a relative one is skipped (it
    would name a different directory wherever the command runs).
    """
    env = os.environ.get(f"UNTAPED_{section.upper()}__{key.upper()}")
    found: list[Path] = []
    for value in (default, *retired_values(section, key), env):
        if not isinstance(value, str) or not value.strip():
            continue
        path = Path(value).expanduser()
        if path.is_absolute() and path not in found:
            found.append(path)
    return found


def overlapping(left: Path, right: Path) -> bool:
    """Whether one of the two directories is the other or lies inside it (symlinks resolved)."""
    a, b = _real(left), _real(right)
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def unsafe_dir(path: Path) -> str | None:
    """Why ``path`` must never be deleted or moved whole, or ``None``.

    It holds the home directory or untaped's config directory, or overlaps
    the plugins' own data (``~/.untaped/plugins``); a symlink is judged by
    what it points to.
    """
    real = _real(path)
    untaped = Path(DEFAULT_CONFIG_PATH).expanduser().parent
    if _real(Path.home()).is_relative_to(real):
        return "it holds your home directory"
    if _real(untaped).is_relative_to(real):
        return f"it holds {shown_path(untaped)}"
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
    ``unchanged`` when none is left). A symlink, a path :func:`unsafe_dir`
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
        freed, done = 0, []
        for path in existing:
            size = dir_bytes(path)
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError as exc:
                detail = f"could not delete {shown_path(path)}: {exc.strerror or exc}"
                return [MigrationOutcome(id=id, action="failed", detail=detail)]
            freed += size
            done.append(shown_path(path))
        text = f"deleted {', '.join(done)} ({size_text(freed)} freed)"
        return [MigrationOutcome(id=id, action="deleted", detail=text)]

    return DirMigration(id=id, title=title, preview=preview, apply=apply)


def _existing(paths: Sequence[Path]) -> list[Path]:
    """The paths that exist, once each."""
    seen: list[Path] = []
    for path in paths:
        if (path.exists() or path.is_symlink()) and path not in seen:
            seen.append(path)
    return seen


def _real(path: Path) -> Path:
    return Path(os.path.realpath(path.expanduser()))
