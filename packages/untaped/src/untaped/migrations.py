"""Building blocks for ``PluginSpec.migrations`` rows (``untaped setup migrate-dirs``).

:func:`delete_migration` is the whole migration for the commonest case: an
old directory nothing reads any more, deleted with a preview. :func:`old_dirs`
finds an older version's directory, custom ones included, and
:func:`dir_bytes` measures what a row moves or frees.
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
    """The directory an older version used: ``default``, then each other one config.yml names.

    ``<section>.<key>`` is the deleted setting that could move it (read with
    :func:`~untaped.deprecated_keys.retired_values`, every profile); paths
    are expanded (``~``) and listed once.
    """
    found: list[Path] = []
    for value in (default, *retired_values(section, key)):
        if not isinstance(value, str) or not value.strip():
            continue
        path = Path(value).expanduser()
        if path not in found:
            found.append(path)
    return found


def delete_migration(
    id: str,
    title: str,
    paths: Callable[[], Sequence[Path]],
    *,
    detail: str = "",
) -> DirMigration:
    """A migration that deletes ``paths()`` (directories or files) when they exist.

    The preview has one ``delete`` row per existing path, with its size and
    ``detail``; applying deletes them and reports ``deleted`` (or
    ``unchanged`` when none is left). The home directory, or a directory
    holding it, is never deleted, whatever ``paths()`` returns. ``paths``
    runs at preview and at apply time, so it may read settings.
    """

    def preview(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationRow]:
        return [
            MigrationRow(action="delete", source=str(path), detail=detail, bytes=dir_bytes(path))
            for path in _existing(paths())
        ]

    def apply(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationOutcome]:
        existing = _existing(paths())
        if not existing:
            return [MigrationOutcome(id=id, action="unchanged", detail="nothing left to delete")]
        freed, done = 0, []
        for path in existing:
            size = dir_bytes(path)
            try:
                if path.is_dir() and not path.is_symlink():
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
    """The paths that exist, once each; never the home directory or one holding it."""
    home = Path.home().resolve()
    seen: list[Path] = []
    for path in paths:
        if not (path.exists() or path.is_symlink()) or path in seen:
            continue
        if home.is_relative_to(path.resolve()):
            continue  # a custom setting naming ~ or / is never a directory to delete
        seen.append(path)
    return seen
