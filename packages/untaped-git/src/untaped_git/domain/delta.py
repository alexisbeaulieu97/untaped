"""What a fetch changed in the caller's namespace.

``fetch()`` returns a :class:`RefDelta`, computed the same way for every call
from the namespace's refs before and after it: refs ``added`` (ref → oid),
``moved`` (ref → old and new oid) and ``pruned`` (ref → the oid it had), named
``heads/<branch>`` and ``tags/<tag>``. A ref git left unchanged is in no list,
so an empty delta means nothing moved.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class RefMove:
    """A ref that moved: the oid it had and the one it has."""

    old: str
    new: str


def _empty() -> Mapping[str, str]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class RefDelta:
    """Refs one fetch added, moved and pruned in the caller's namespace."""

    added: Mapping[str, str] = field(default_factory=_empty)
    moved: Mapping[str, RefMove] = field(default_factory=lambda: MappingProxyType({}))
    pruned: Mapping[str, str] = field(default_factory=_empty)

    def __bool__(self) -> bool:
        return bool(self.added or self.moved or self.pruned)


def diff_refs(before: Mapping[str, str], after: Mapping[str, str]) -> RefDelta:
    """The delta between two snapshots of one namespace (ref → oid)."""
    added = {ref: oid for ref, oid in after.items() if ref not in before}
    pruned = {ref: oid for ref, oid in before.items() if ref not in after}
    moved = {
        ref: RefMove(old, after[ref])
        for ref, old in before.items()
        if ref in after and after[ref] != old
    }
    return RefDelta(
        MappingProxyType(dict(sorted(added.items()))),
        MappingProxyType(dict(sorted(moved.items()))),
        MappingProxyType(dict(sorted(pruned.items()))),
    )
