"""The recipe plugin as a whole: lint, its plugin check, its surface, the root mounting it."""

from __future__ import annotations

import json

from untaped.sdk.testing import assert_surface, check_plugin, invoke_root
from untaped_lint.testing import assert_clean
from untaped_recipe.plugin import RecipePlugin


def test_recipe_passes_lint() -> None:
    assert_clean("recipe")


def test_recipe_passes_its_plugin_check() -> None:
    failed = [row for row in check_plugin("recipe") if row.status in ("fail", "error")]
    assert failed == []


def test_recipe_keeps_its_surface() -> None:
    assert_surface(RecipePlugin)  # snapshots/surface.txt beside this file


def test_the_root_mounts_the_plugin() -> None:
    listed = invoke_root(["plugin", "list", "--format", "json"])
    assert ("recipe", "ready") in [(r["name"], r["status"]) for r in json.loads(listed.stdout)]
    assert "apply" in invoke_root(["recipe", "--help"]).stdout
