"""Map a spec's ``apply_strategy`` name to a concrete strategy instance."""

from __future__ import annotations

from typing import TYPE_CHECKING

from untaped.capabilities.awx.errors import AwxApiError
from untaped.capabilities.awx.infrastructure.strategies import (
    DefaultApplyStrategy,
    InventoryApplyStrategy,
    InventoryChildApplyStrategy,
    ScheduleApplyStrategy,
)

if TYPE_CHECKING:
    from untaped.capabilities.awx.application.ports import ApplyStrategy


class StaticStrategyResolver:
    def __init__(self) -> None:
        self._registry: dict[str, ApplyStrategy] = {
            "default": DefaultApplyStrategy(),
            "schedule": ScheduleApplyStrategy(),
            "inventory": InventoryApplyStrategy(),
            "inventory_child": InventoryChildApplyStrategy(),
        }

    def get(self, name: str) -> ApplyStrategy:
        try:
            return self._registry[name]
        except KeyError as exc:
            raise AwxApiError(
                f"unknown apply strategy {name!r} (available: {', '.join(sorted(self._registry))})"
            ) from exc
