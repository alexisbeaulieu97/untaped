"""Unified-diff rendering over raw strings (no file-change model coupling)."""

from __future__ import annotations

import difflib
from dataclasses import dataclass


@dataclass(frozen=True)
class DiffStats:
    """Line counts for a before→after change."""

    added: int
    removed: int


def _split(text: str | None) -> list[str]:
    return [] if text is None else text.splitlines(keepends=True)


def unified_diff_text(before: str | None, after: str | None, *, path: str) -> str:
    """Render a unified diff with patch-compatible ``a/``/``b/`` headers.

    ``None`` on either side means the file does not exist there (created /
    deleted). Identical content renders as the empty string.
    """
    lines = difflib.unified_diff(
        _split(before),
        _split(after),
        fromfile="/dev/null" if before is None else f"a/{path}",
        tofile="/dev/null" if after is None else f"b/{path}",
    )
    out: list[str] = []
    for line in lines:
        if line.endswith("\n"):
            out.append(line)
        else:
            out.append(f"{line}\n")
            out.append("\\ No newline at end of file\n")
    return "".join(out)


def diff_stats(before: str | None, after: str | None) -> DiffStats:
    """Count added/removed lines between ``before`` and ``after``."""
    added = 0
    removed = 0
    for index, line in enumerate(difflib.unified_diff(_split(before), _split(after), lineterm="")):
        if index < 2:
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return DiffStats(added=added, removed=removed)
