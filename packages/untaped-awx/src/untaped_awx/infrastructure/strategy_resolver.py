"""Map a spec's ``apply_strategy`` name to a concrete strategy instance."""

from __future__ import annotations

from untaped_awx.application.ports import ApplyStrategy
from untaped_awx.errors import AwxApiError
from untaped_awx.infrastructure.strategies import (
    DefaultApplyStrategy,
    ScheduleApplyStrategy,
)


class StaticStrategyResolver:
    def __init__(self) -> None:
        self._registry: dict[str, ApplyStrategy] = {
            "default": DefaultApplyStrategy(),
            "schedule": ScheduleApplyStrategy(),
        }

    def get(self, name: str) -> ApplyStrategy:
        try:
            return self._registry[name]
        except KeyError as exc:
            raise AwxApiError(
                f"unknown apply strategy {name!r} (available: {', '.join(sorted(self._registry))})"
            ) from exc
