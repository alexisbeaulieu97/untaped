"""Constructed inventory field ownership and immutable generated-source fields."""

from collections.abc import Mapping
from typing import Any

CONSTRUCTED_SOURCE_FIELDS = ("source_vars", "update_cache_timeout", "limit", "verbosity")


def inventory_read_only_fields(kind: str, record: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the immutable fields added by a concrete inventory/source form."""
    if kind == "Inventory" and record.get("kind") == "constructed":
        return ("host_filter",)
    if kind == "InventorySource" and record.get("source") == "constructed":
        return ("name", "source")
    return ()
