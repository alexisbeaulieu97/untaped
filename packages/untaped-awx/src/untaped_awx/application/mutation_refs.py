"""Typed references used while preparing a multi-resource AWX mutation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DeferredReference:
    """A planned resource reference whose controller ID is not known yet."""

    token: str
    kind: str
    name: str
    scope: dict[str, str]

    def __hash__(self) -> int:
        return hash(self.token)


PlannedId = int | DeferredReference


def tokens_in(value: Any) -> set[str]:
    """Every planned token ``value`` holds, however deeply nested."""
    if isinstance(value, DeferredReference):
        return {value.token}
    if isinstance(value, Mapping):
        result: set[str] = set()
        for item in value.values():
            result.update(tokens_in(item))
        return result
    if isinstance(value, list | tuple | set | frozenset):
        sequence_tokens: set[str] = set()
        for item in value:
            sequence_tokens.update(tokens_in(item))
        return sequence_tokens
    return set()


__all__ = ["DeferredReference", "PlannedId", "tokens_in"]
