"""Filesystem helpers for SDK commands: structured reads and durable atomic writes."""

from __future__ import annotations

import json
import os
import stat
import uuid
from collections.abc import Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from untaped.errors import ConfigError, UntapedError


class FileWriteError(UntapedError):
    """A planned write could not be applied safely.

    ``rollback_incomplete`` is ``True`` when the transaction failed AND
    restoring the already-applied changes also failed — the caller must tell
    the user the tree is dirty.
    """

    def __init__(self, message: str, *, rollback_incomplete: bool = False) -> None:
        super().__init__(message)
        self.rollback_incomplete = rollback_incomplete


@dataclass(frozen=True)
class FileChange:
    """One planned change: ``before`` is the expected current content
    (``None`` = file must not exist); ``after`` is the new content
    (``None`` = delete)."""

    path: Path
    before: str | None
    after: str | None


def atomic_write(
    path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
    newline: str = "",
    mode: int | None = None,
) -> None:
    """Write ``content`` to ``path`` atomically and durably.

    The content goes to a temp file beside the target, is fsynced, then
    ``os.replace``-d over the target, and the directory is fsynced where the
    platform allows it; a failed write leaves the original untouched and no
    temp file behind. A symlinked ``path`` is written through: the link
    survives and the file it points at gets the content. An existing file
    keeps its permission bits; ``mode`` sets them instead (the temp file never
    has looser ones, even briefly); a new file otherwise gets the default mode
    (``0o666`` minus the umask). Creates ``path``'s missing parent
    directories, never those of a symlink's target: a link into a missing
    directory raises :class:`FileNotFoundError`. ``newline=""`` disables
    newline translation so the caller's line endings land on disk verbatim.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    target = _write_target(path)
    tmp = _write_temp(target, content, encoding=encoding, newline=newline, mode=mode)
    try:
        _commit(tmp, target)
    finally:
        _remove_staged((tmp,))


def _write_target(path: Path) -> Path:
    """The file a write to ``path`` must replace: a symlink's final target."""
    return Path(os.path.realpath(path)) if path.is_symlink() else path


def _write_temp(
    target: Path,
    content: str,
    *,
    encoding: str = "utf-8",
    newline: str = "",
    mode: int | None = None,
) -> Path:
    """Write ``content`` to a fresh fsynced temp file beside ``target``; return it.

    The temp file gets ``mode``, else ``target``'s current mode, else the
    default mode for a new file.
    """
    if mode is None:
        with suppress(FileNotFoundError):
            mode = stat.S_IMODE(target.stat().st_mode)
    create_mode = 0o666 if mode is None else 0o600
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.untaped.tmp")
    try:
        with open(
            tmp,
            "x",
            encoding=encoding,
            newline=newline,
            opener=lambda name, flags: os.open(name, flags, create_mode),
        ) as handle:
            if mode is not None:
                os.chmod(handle.fileno(), mode)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        _remove_staged((tmp,))
        raise
    return tmp


def _commit(tmp: Path, target: Path) -> None:
    """Move a staged temp file over ``target`` and persist the rename."""
    os.replace(tmp, target)
    with suppress(OSError):  # directories cannot be opened or fsynced everywhere
        fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def apply_file_changes(changes: Sequence[FileChange]) -> None:
    """Apply ``changes`` as one transaction: all land, or all roll back.

    Verifies each target still matches its ``before`` content, stages every
    replacement next to its target, then swaps them in. On failure the
    already-applied changes are restored in reverse order; if that restore
    itself fails, the raised :class:`FileWriteError` has
    ``rollback_incomplete=True``.
    """
    _verify_current_content(changes)
    staged = _stage_replacements(changes)
    applied: list[FileChange] = []
    try:
        for index, change in enumerate(changes):
            if change.after is None:
                if change.path.exists():
                    change.path.unlink()
                applied.append(change)
                continue
            _commit(staged[index], _write_target(change.path))
            applied.append(change)
    except BaseException as exc:
        _remove_staged(staged.values())
        rollback_errors = _rollback(applied)
        if rollback_errors:
            details = "; ".join(rollback_errors)
            raise FileWriteError(
                f"{exc}; rollback incomplete: {details}", rollback_incomplete=True
            ) from exc
        if isinstance(exc, OSError):
            raise FileWriteError(str(exc)) from exc
        raise
    finally:
        _remove_staged(staged.values())


def _read_verbatim(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def _verify_current_content(changes: Sequence[FileChange]) -> None:
    for change in changes:
        try:
            current = _read_verbatim(change.path) if change.path.is_file() else None
        except OSError as exc:
            raise FileWriteError(str(exc)) from exc
        if current != change.before:
            raise FileWriteError(f"{change.path} changed since planning")


def _stage_replacements(changes: Sequence[FileChange]) -> dict[int, Path]:
    staged: dict[int, Path] = {}
    try:
        for index, change in enumerate(changes):
            if change.after is None:
                continue
            change.path.parent.mkdir(parents=True, exist_ok=True)
            staged[index] = _write_temp(_write_target(change.path), change.after)
    except BaseException as exc:
        _remove_staged(staged.values())
        if isinstance(exc, OSError):
            raise FileWriteError(str(exc)) from exc
        raise
    return staged


def _rollback(applied: list[FileChange]) -> list[str]:
    errors: list[str] = []
    for change in reversed(applied):
        try:
            if change.before is None:
                # Remove what was created, not a (formerly dangling) link to it.
                created = _write_target(change.path)
                if created.exists():
                    created.unlink()
                continue
            atomic_write(change.path, change.before)
        except OSError as exc:
            errors.append(f"{change.path}: {exc}")
    return errors


def _remove_staged(paths: Iterable[Path]) -> None:
    for path in paths:
        with suppress(OSError):
            path.unlink(missing_ok=True)


def read_structured_file(path: Path) -> dict[str, Any]:
    """Read a YAML-or-JSON mapping file (``.json`` suffix → JSON parser).

    Raises :class:`ConfigError` on read failure, parse failure, or a
    non-mapping document. An empty document is an empty dict.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"could not read {path}: {exc}") from exc
    try:
        raw = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"could not parse {path}: {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain an object")
    return dict(raw)
