"""The values plugin as a whole: lint, its plugin check, its surface, the root listing it."""

from __future__ import annotations

import json

from untaped.sdk.testing import assert_surface, check_plugin, invoke_root
from untaped_lint.testing import assert_clean
from untaped_values.plugin import Values


def test_values_passes_lint() -> None:
    assert_clean("values")


def test_values_passes_its_plugin_check() -> None:
    failed = [row for row in check_plugin("values") if row.status in ("fail", "error")]
    assert failed == []


def test_values_keeps_its_surface() -> None:
    assert_surface(Values)  # snapshots/surface.txt beside this file


def test_the_root_lists_the_plugin() -> None:
    listed = invoke_root(["plugin", "list", "--format", "json"])
    assert ("values", "ready") in [(r["name"], r["status"]) for r in json.loads(listed.stdout)]
