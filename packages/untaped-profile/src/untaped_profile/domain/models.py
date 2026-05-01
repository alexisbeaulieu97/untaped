"""Domain entities for the profile bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Profile:
    """One profile entry with its raw YAML data and metadata.

    ``data`` is the verbatim ``profiles.<name>`` block (no fallback merge);
    use cases that need the resolved view ask the repository for it.
    """

    name: str
    data: dict[str, Any]
    is_active: bool

    @property
    def key_count(self) -> int:
        """Number of leaf keys this profile sets (for the list table)."""
        return _count_leaves(self.data)


def _count_leaves(data: dict[str, Any]) -> int:
    total = 0
    for value in data.values():
        if isinstance(value, dict):
            total += _count_leaves(value)
        else:
            total += 1
    return total
