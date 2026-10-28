"""The lint plugin as a whole: lint, its plugin check, its surface, the root mounting it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from untaped.sdk.testing import assert_surface, check_plugin, invoke_root
from untaped_lint.plugin import LintPlugin
from untaped_lint.testing import assert_clean

pytestmark = pytest.mark.usefixtures("fresh_composition")


def test_lint_passes_lint() -> None:
    assert_clean("lint", tests_dir=Path(__file__).resolve().parents[1])


def test_lint_passes_its_plugin_check() -> None:
    failed = [row for row in check_plugin("lint") if row.status in ("fail", "error")]
    assert failed == []


def test_lint_keeps_its_surface() -> None:
    assert_surface(LintPlugin)  # snapshots/surface.txt beside this file


def test_the_root_mounts_the_plugin() -> None:
    listed = invoke_root(["plugin", "list", "--format", "json"])
    assert ("lint", "ready") in [(r["name"], r["status"]) for r in json.loads(listed.stdout)]
    assert "rules" in invoke_root(["lint", "--help"]).stdout


def test_the_rule_module_guide_prints_its_examples_whole() -> None:
    page = invoke_root(["docs", "lint", "guides", "rule-module"]).stdout
    assert "```python\n# .untaped/lint/no_print.py\n" in page
    assert "def test_no_print_passes_the_good_example() -> None:" in page
    assert "`untaped docs lint inputs lint_file`.\n\n## Options" in page
