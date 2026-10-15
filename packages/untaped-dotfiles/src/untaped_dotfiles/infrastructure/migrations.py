"""``setup migrate-dirs``: 10.x's ``~/.untaped/dotfiles`` into ``plugins/dotfiles/``.

The directory's entries move (clones, kept files), but three things outside
it point in: ``state.yml``'s managed repo ``path``, each ``link`` record's
``source_hash``/``target_hash`` (the link's destination), and the symlinks
themselves. Applying, under the lock 11.0's commands take
(``plugins/dotfiles/.lock``), moves the old directory's entries into the new
one, repoints every recorded symlink that still points into the old place,
then rewrites state. Symlinks go before state because state is what finds
them: a crash anywhere leaves either entries still in the old directory or
state still naming it, so the next run finishes. What an 11.0 command
already wrote in the new directory (the status files, the lock) is
replaced; an entry both directories hold (``repos`` from an 11.0
``subscribe``) is a ``keep`` row, never merged. Paths are rebased in both
spellings state can hold, as written and with symlinks resolved (the
commands resolve theirs).

A profile that still sets a directory setting into the old place keeps it,
as a ``keep`` row, to be moved by hand.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from untaped.sdk import (
    MigrationOptions,
    MigrationOutcome,
    MigrationRow,
    PluginContext,
    StateCollection,
    dir_bytes,
    plural,
    setting_guard,
    shown_path,
)
from untaped_dotfiles.infrastructure.placer import replace_symlink
from untaped_dotfiles.infrastructure.state_store import (
    ATTENTION_FILE,
    STATUS_FILE,
    StateDotfilesStore,
)
from untaped_dotfiles.settings import DotfilesSettings

ID = "dotfiles.data"
_OLD = Path("~/.untaped/dotfiles")
_DIR_KEYS = ("repos_dir", "kept_dir", "state_dir")
#: What an 11.0 command writes in the new directory before the migration
#: runs (``status`` and the lock): rewritten on the next run, so dropped.
_REGENERATED = frozenset({".lock", STATUS_FILE, ATTENTION_FILE})


def _old() -> Path:
    return _OLD.expanduser()


def _new() -> Path:
    """The 11.0 directory: ``dotfiles.state_dir``'s default, ``plugin_dir`` of dotfiles."""
    default: Path = DotfilesSettings.model_fields["state_dir"].default
    return default.expanduser()


def _prefixes() -> list[tuple[Path, Path]]:
    """``(old, new)`` in both spellings: as written, and with ``~/.untaped`` resolved."""
    old, new = _old(), _new()
    real = Path(os.path.realpath(old.parent))
    pairs = [(old, new)]
    if real != old.parent:
        pairs.append((real / old.name, real / new.relative_to(old.parent)))
    return pairs


def _rebase(value: object, prefixes: Sequence[tuple[Path, Path]]) -> str | None:
    """``value`` moved from the old directory to the new one; ``None`` when it isn't inside."""
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    for old, new in prefixes:
        if path.is_relative_to(old):
            return str(new / path.relative_to(old))
    return None


def _conflicts(old: Path, new: Path) -> list[Path]:
    """Entries both directories hold (regenerated files aside): never merged."""
    try:
        names = [entry.name for entry in old.iterdir() if entry.name not in _REGENERATED]
    except OSError:
        return []
    return [
        new / name for name in sorted(names) if (new / name).exists() or (new / name).is_symlink()
    ]


_REPOS = StateCollection("dotfiles", "repos", id_field="name")
_APPLIED = StateCollection("dotfiles", "applied", id_field="target")


def _repo_row(row: dict[str, Any], prefixes: Sequence[tuple[Path, Path]]) -> dict[str, Any]:
    path = _rebase(row.get("path"), prefixes) if row.get("managed") else None
    return row if path is None else {**row, "path": path}


def _applied_row(row: dict[str, Any], prefixes: Sequence[tuple[Path, Path]]) -> dict[str, Any]:
    if row.get("mode") != "link":
        return row
    changed = {
        key: rebased
        for key in ("source_hash", "target_hash")
        if (rebased := _rebase(row.get(key), prefixes)) is not None
    }
    return {**row, **changed} if changed else row


def _stale_links(prefixes: Sequence[tuple[Path, Path]]) -> list[tuple[Path, str]]:
    """Each recorded link still pointing into the old directory, with where it should point."""
    found = []
    for row in _APPLIED.entries():
        if row.get("mode") != "link":
            continue
        target = Path(str(row.get("target", "")))
        try:
            pointing = os.readlink(target)
        except OSError:
            continue  # gone or not a link: status reports it, the migration leaves it
        rebased = _rebase(pointing, prefixes)
        if rebased is not None:
            found.append((target, rebased))
    return found


def _pending(prefixes: Sequence[tuple[Path, Path]]) -> tuple[int, int]:
    """``(state rows, symlinks)`` still naming the old directory."""
    rows = sum(1 for row in _REPOS.entries() if _repo_row(row, prefixes) is not row)
    rows += sum(1 for row in _APPLIED.entries() if _applied_row(row, prefixes) is not row)
    return rows, len(_stale_links(prefixes))


def preview(_ctx: PluginContext, options: MigrationOptions) -> Sequence[MigrationRow]:
    old, new = _old(), _new()
    if old.is_symlink():
        target = shown_path(os.path.realpath(old))
        detail = f"kept: a symlink to {target}; its directory already stays where it is"
        return [MigrationRow(action="keep", source=str(old), detail=detail)]
    if old.exists():
        reason = setting_guard("dotfiles", *_DIR_KEYS)(old)
        if reason is None and (taken := _conflicts(old, new)):
            reason = f"{shown_path(taken[0])} already exists; keep one of them"
        if reason is not None:
            return [MigrationRow(action="keep", source=str(old), detail=f"kept: {reason}")]
    rows, links = _pending(_prefixes())
    if not old.exists() and not rows and not links:
        return []
    detail = f"{plural(links, 'link')} repointed, {plural(rows, 'state record')} updated"
    size = dir_bytes(old) if options.measure and old.exists() else 0
    move = MigrationRow(
        action="move", source=str(old), destination=str(new), detail=detail, bytes=size
    )
    if not old.exists():
        return [move]
    prompt = MigrationRow(
        action="then",
        detail=(
            f"a prompt or status line reading {shown_path(old / ATTENTION_FILE)} must read "
            f"{shown_path(new / ATTENTION_FILE)}"
        ),
    )
    return [move, prompt]


def apply(ctx: PluginContext, options: MigrationOptions) -> Sequence[MigrationOutcome]:
    quick = options.model_copy(update={"measure": False})
    if not any(row.action == "move" for row in preview(ctx, quick)):
        return [MigrationOutcome(id=ID, action="unchanged", detail="nothing left to move")]
    old, new = _old(), _new()
    with StateDotfilesStore(state_dir=new).locked():
        moved = old.is_dir()
        if moved:
            for entry in sorted(old.iterdir()):
                if entry.name in _REGENERATED:
                    entry.unlink()  # 10.x's status files: the next command writes new ones
                else:
                    shutil.move(entry, new / entry.name)
            old.rmdir()
        prefixes = _prefixes()
        links = _stale_links(prefixes)
        for target, pointing in links:
            replace_symlink(Path(pointing), target)
        _REPOS.mutate(_rows(_repo_row, prefixes))
        _APPLIED.mutate(_rows(_applied_row, prefixes))
    parts = [f"moved {shown_path(old)} to {shown_path(new)}"] if moved else []
    parts.append(f"repointed {plural(len(links), 'link')}")
    return [MigrationOutcome(id=ID, action="moved", detail="; ".join(parts))]


def _rows(
    fix: Callable[[dict[str, Any], Sequence[tuple[Path, Path]]], dict[str, Any]],
    prefixes: Sequence[tuple[Path, Path]],
) -> Callable[[list[dict[str, Any]]], list[dict[str, Any]]]:
    return lambda rows: [fix(row, prefixes) for row in rows]
