"""Formatting helpers for destructive prune safety checks."""

from __future__ import annotations


def format_prune_blockers(blockers: tuple[str, ...]) -> str:
    first = blockers[0]
    if len(blockers) == 1:
        return f"unsafe local state: {first}"
    return f"unsafe local state: {first}; +{len(blockers) - 1} more"


def format_all_prune_blockers(blockers: tuple[str, ...]) -> str:
    return "unsafe local state: " + "; ".join(blockers)
