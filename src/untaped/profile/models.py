"""Domain entities for the profile bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from untaped.records import OutcomeRecord


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


@dataclass(frozen=True)
class ProfileDeletePreview:
    """Non-secret summary shown before confirming profile deletion."""

    name: str
    top_level_keys: tuple[str, ...]


class ProfileOutcome(OutcomeRecord):
    """The result of ``profile create/delete/rename`` (``untaped.profile_outcome``).

    ``action`` is ``created``, ``deleted``, ``renamed``, or ``planned`` under
    ``--dry-run``. ``previous_name`` is set by ``rename`` and ``copied_from``
    by ``create --copy-from``.
    """

    name: str
    previous_name: str | None = None
    copied_from: str | None = None


def _count_leaves(data: dict[str, Any]) -> int:
    total = 0
    for value in data.values():
        if isinstance(value, dict):
            total += _count_leaves(value)
        else:
            total += 1
    return total
