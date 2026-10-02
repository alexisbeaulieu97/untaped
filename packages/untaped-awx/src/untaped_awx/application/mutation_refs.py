"""Typed references used while preparing a multi-resource AWX mutation."""

from __future__ import annotations

from dataclasses import dataclass


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


__all__ = ["DeferredReference", "PlannedId"]
