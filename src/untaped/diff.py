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
    return "".join(
        difflib.unified_diff(
            _split(before),
            _split(after),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def diff_stats(before: str | None, after: str | None) -> DiffStats:
    """Count added/removed lines between ``before`` and ``after``."""
    added = 0
    removed = 0
    for line in difflib.unified_diff(_split(before), _split(after), lineterm=""):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return DiffStats(added=added, removed=removed)
