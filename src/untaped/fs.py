"""Filesystem input helpers for SDK commands."""

from __future__ import annotations

import json
import os
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


def atomic_write(path: Path, content: str, *, encoding: str = "utf-8", newline: str = "") -> None:
    """Write ``content`` to ``path`` atomically (temp file + ``os.replace``).

    Creates parent directories. ``newline=""`` disables newline translation
    so the caller's line endings land on disk verbatim.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.untaped.tmp")
    try:
        with open(tmp, "w", encoding=encoding, newline=newline) as handle:
            handle.write(content)
        os.replace(tmp, path)
    finally:
        with suppress(OSError):
            tmp.unlink(missing_ok=True)


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
        for change in changes:
            if change.after is None:
                if change.path.exists():
                    change.path.unlink()
                applied.append(change)
                continue
            os.replace(staged[change], change.path)
            applied.append(change)
    except OSError as exc:
        _remove_staged(staged.values())
        rollback_errors = _rollback(applied)
        if rollback_errors:
            details = "; ".join(rollback_errors)
            raise FileWriteError(
                f"{exc}; rollback incomplete: {details}", rollback_incomplete=True
            ) from exc
        raise FileWriteError(str(exc)) from exc
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


def _stage_replacements(changes: Sequence[FileChange]) -> dict[FileChange, Path]:
    staged: dict[FileChange, Path] = {}
    try:
        for change in changes:
            if change.after is None:
                continue
            change.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = change.path.with_name(f".{change.path.name}.{uuid.uuid4().hex}.untaped.tmp")
            with open(tmp, "w", encoding="utf-8", newline="") as handle:
                handle.write(change.after)
            staged[change] = tmp
    except OSError as exc:
        _remove_staged(staged.values())
        raise FileWriteError(str(exc)) from exc
    return staged


def _rollback(applied: list[FileChange]) -> list[str]:
    errors: list[str] = []
    for change in reversed(applied):
        tmp: Path | None = None
        try:
            if change.before is None:
                if change.path.exists():
                    change.path.unlink()
                continue
            change.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = change.path.with_name(
                f".{change.path.name}.{uuid.uuid4().hex}.untaped.rollback.tmp"
            )
            with open(tmp, "w", encoding="utf-8", newline="") as handle:
                handle.write(change.before)
            os.replace(tmp, change.path)
        except OSError as exc:
            errors.append(f"{change.path}: {exc}")
        finally:
            if tmp is not None:
                _remove_staged((tmp,))
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
