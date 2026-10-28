"""``awx.empty-inventory``: an inventory with no hosts, so its jobs match nothing."""

from __future__ import annotations

from collections.abc import Iterator

from untaped.sdk import experimental
from untaped_awx.domain.lint.resources import Resources
from untaped_awx.domain.lint.rules._support import report
from untaped_lint.api import Finding, rule


@experimental
@rule(severity="warning")
def empty_inventory(resources: Resources) -> Iterator[Finding]:
    """An inventory on the controller holds at least one host."""
    for item in resources.of("Inventory"):
        if item.controller is not None and item.controller.total_hosts == 0:
            yield report(
                empty_inventory,
                item,
                "the inventory has no hosts",
                hint="sync its sources, or remove it if nothing uses it",
            )
