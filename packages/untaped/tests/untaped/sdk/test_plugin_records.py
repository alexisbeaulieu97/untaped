"""``installed_plugins``: every candidate the composition read, ready or quarantined."""

from __future__ import annotations

import pytest

from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginCandidate
from untaped.sdk.plugin_records import installed_plugins

pytestmark = pytest.mark.usefixtures("fresh_composition")


def test_the_records_come_from_the_candidates_composed() -> None:
    ghost = PluginCandidate(distribution="untaped-ghost", name="ghost", target="ghost:SPEC")
    bootstrap.compose_root(candidates=[ghost])
    record, core = installed_plugins()
    assert (record.name, record.status, record.package) == ("ghost", "quarantined", "ghost")
    assert (core.name, core.status, core.root, record.root) == ("untaped", "ready", True, False)
