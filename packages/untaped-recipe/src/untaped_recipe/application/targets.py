"""Recipe target directories, from paths or the rows piped to ``apply``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel


@dataclass(frozen=True)
class Target:
    """One target directory plus optional pipe-record context."""

    path: Path
    record: Mapping[str, object] | None = None


def target_of(row: BaseModel) -> Target:
    """The target a piped row names: its absolute ``target_path``, else its ``path``."""
    record = row.model_dump(mode="json")
    target_path = record.get("target_path")
    if target_path is not None:
        if not isinstance(target_path, str) or not target_path.strip():
            raise ValueError("a row's target_path must be a non-empty string")
        path = Path(target_path.strip())
        if not path.is_absolute():
            raise ValueError(f"a row's target_path must be absolute, not {target_path!r}")
        return Target(path=path, record=record)
    value = record.get("path")
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError("a piped row names no target: it has no target_path or path")
    return Target(path=Path(text), record=record)


def dedupe_targets(targets: list[Target]) -> list[Target]:
    """Drop targets naming an already-seen directory, keeping first-seen order.

    ``./a``, ``a/`` and ``/abs/a`` (or a symlink to it) are one directory;
    planning it twice would make the second flush fail "changed since
    planning" after the first one wrote.
    """
    seen: set[Path] = set()
    unique: list[Target] = []
    for target in targets:
        key = target.path.expanduser().resolve()
        if key in seen:
            continue
        seen.add(key)
        unique.append(target)
    return unique
