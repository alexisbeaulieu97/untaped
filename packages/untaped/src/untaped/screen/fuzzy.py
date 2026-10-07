"""Fuzzy ranking for searchable lists: subsequence scoring with matched positions.

:func:`rank` orders anything that has a ``label``, a ``description`` and a
``dimmed`` flag (the picker's ``PickItem``, a component's ``ListItem``), so
every searchable list matches the same way. It imports nothing from Rich or
prompt_toolkit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

__all__ = ["Match", "Rankable", "Ranked", "fuzzy_match", "rank"]

_BOUNDARIES = frozenset("/-_. ")


class Rankable(Protocol):
    """What :func:`rank` reads from an item: the text it matches and whether it sorts last."""

    @property
    def label(self) -> str:
        """The text a term matches fuzzily (and the one a view highlights)."""
        ...

    @property
    def description(self) -> str:
        """Longer text a term can match as a substring."""
        ...

    @property
    def dimmed(self) -> bool:
        """Whether the item is unavailable and sorts after every other match."""
        ...


@dataclass(frozen=True)
class Match:
    """How well a query matched, and which characters of the text it hit."""

    score: int
    positions: tuple[int, ...]


@dataclass(frozen=True)
class Ranked[T: Rankable]:
    """An item that matched, with the label positions to highlight."""

    item: T
    positions: tuple[int, ...]


def fuzzy_match(query: str, text: str) -> Match | None:
    """Score ``text`` against ``query``, or ``None`` when its letters are not all there in order.

    Case-insensitive. A whole substring scores highest (more at a word
    boundary); otherwise letters match greedily left to right, earning a bonus
    for consecutive letters and for letters at a boundary (``/-_.`` or space).
    Longer texts lose a little so short exact names win ties.
    """
    if not query:
        return Match(0, ())
    needle, haystack = query.lower(), text.lower()
    start = haystack.find(needle)
    if start >= 0:
        boundary = 8 if start == 0 or haystack[start - 1] in _BOUNDARIES else 0
        positions = tuple(range(start, start + len(needle)))
        return Match(20 + 6 * len(needle) + boundary - len(haystack) // 10, positions)
    return _subsequence(needle, haystack)


def _subsequence(needle: str, haystack: str) -> Match | None:
    positions: list[int] = []
    score = 0
    cursor = 0
    for char in needle:
        index = haystack.find(char, cursor)
        if index < 0:
            return None
        score += 1
        if positions and index == positions[-1] + 1:
            score += 5
        if index == 0 or haystack[index - 1] in _BOUNDARIES:
            score += 8
        positions.append(index)
        cursor = index + 1
    return Match(score - len(haystack) // 10, tuple(positions))


def rank[T: Rankable](query: str, items: Sequence[T]) -> list[Ranked[T]]:
    """Items matching every whitespace-separated term, best first, dimmed last.

    A term matches the label fuzzily or the description as a substring (worth
    less). With no terms, the catalog order is kept, dimmed items last.
    """
    terms = query.lower().split()
    scored: list[tuple[bool, int, int, Ranked[T]]] = []
    for index, item in enumerate(items):
        found = _score(terms, item)
        if found is not None:
            total, positions = found
            scored.append((item.dimmed, -total, index, Ranked(item, positions)))
    scored.sort(key=lambda entry: entry[:3])
    return [entry[3] for entry in scored]


def _score(terms: list[str], item: Rankable) -> tuple[int, tuple[int, ...]] | None:
    """Score lowercased ``terms`` against one item, or ``None`` if one misses."""
    total = 0
    positions: set[int] = set()
    for term in terms:
        match = fuzzy_match(term, item.label)
        if match is not None:
            total += match.score
            positions.update(match.positions)
        elif term in item.description.lower():
            total += 1
        else:
            return None
    return total, tuple(sorted(positions))
