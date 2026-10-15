"""``setup migrate-dirs``: 10.x's ``~/.untaped/dotfiles`` into ``plugins/dotfiles/``.

The directory moves whole (clones, kept files, status files), but three
things outside it point in: ``state.yml``'s managed repo ``path``, each
``link`` record's ``source_hash``/``target_hash`` (the link's destination),
and the symlinks themselves. Applying, under the dotfiles lock, moves the
directory, repoints every recorded symlink that still points into the old
place, then rewrites state. Symlinks go before state because state is
what finds them: a crash between the two leaves state still naming the old
place, so the next run finishes. A new directory holding only what an 11.0
command already wrote there (the status files, the lock) is replaced; one
holding anything else is a ``keep`` row. Paths are rebased in both spellings state
can hold, as written and with symlinks resolved (the commands resolve
theirs).

It runs only while all three directory settings keep their defaults; a
profile that set any of them keeps the old directory as a ``keep`` row, to
be moved by hand.
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
    shown_path,
)
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


def _taken(new: Path) -> bool:
    """Whether ``new`` holds anything but files a command regenerates."""
    if new.is_symlink() or new.is_file():
        return True
    try:
        return any(entry.name not in _REGENERATED or not entry.is_file() for entry in new.iterdir())
    except FileNotFoundError:
        return False


def _custom(settings: DotfilesSettings) -> list[str]:
    return [
        key
        for key in _DIR_KEYS
        if getattr(settings, key) != DotfilesSettings.model_fields[key].default
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


def preview(ctx: PluginContext, options: MigrationOptions) -> Sequence[MigrationRow]:
    settings = ctx.settings
    if not isinstance(settings, DotfilesSettings):
        return []
    old, new = _old(), _new()
    if custom := _custom(settings):
        if not old.exists():
            return []
        keys = ", ".join(f"dotfiles.{key}" for key in custom)
        return [MigrationRow(action="keep", source=str(old), detail=f"kept: {keys} is set")]
    if old.is_symlink():
        target = shown_path(os.path.realpath(old))
        detail = f"kept: a symlink to {target}; its directory already stays where it is"
        return [MigrationRow(action="keep", source=str(old), detail=detail)]
    if old.exists() and _taken(new):
        detail = f"kept: {shown_path(new)} already exists; keep one of them"
        return [MigrationRow(action="keep", source=str(old), detail=detail)]
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
    lock_dir = old if old.is_dir() else new
    with StateDotfilesStore(state_dir=lock_dir).locked():
        moved = old.is_dir()
        if moved:
            if new.is_dir():
                for name in _REGENERATED:
                    (new / name).unlink(missing_ok=True)
                new.rmdir()
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(old, new)  # the lock is on the file, which moves with it
        prefixes = _prefixes()
        links = _stale_links(prefixes)
        for target, pointing in links:
            _relink(target, pointing)
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


def _relink(target: Path, pointing: str) -> None:
    """Point the symlink ``target`` at ``pointing``, replacing it in one step."""
    temporary = target.with_name(f".{target.name}.untaped-relink")
    temporary.unlink(missing_ok=True)
    os.symlink(pointing, temporary)
    os.replace(temporary, target)
