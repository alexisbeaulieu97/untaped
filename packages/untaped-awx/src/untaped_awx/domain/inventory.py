"""Inventory-family rules: child kinds, constructed inventories, generated sources."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

INVENTORY_CHILD_KINDS = frozenset({"Host", "Group", "InventorySource"})
"""Kinds owned by an Inventory (their ``parent_field`` is ``inventory``)."""

CONSTRUCTED_SOURCE_FIELDS = ("source_vars", "update_cache_timeout", "limit", "verbosity")


def is_constructed_inventory(kind: str, record: Mapping[str, Any]) -> bool:
    """An Inventory record of the ``constructed`` kind."""
    return kind == "Inventory" and record.get("kind") == "constructed"


def is_generated_source(kind: str, record: Mapping[str, Any]) -> bool:
    """The InventorySource AWX generates for (and owns on) a constructed inventory."""
    return kind == "InventorySource" and record.get("source") == "constructed"


def inventory_read_only_fields(kind: str, record: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the immutable fields added by a concrete inventory/source form."""
    if is_constructed_inventory(kind, record):
        return ("host_filter",)
    if is_generated_source(kind, record):
        return ("name", "source")
    return ()
