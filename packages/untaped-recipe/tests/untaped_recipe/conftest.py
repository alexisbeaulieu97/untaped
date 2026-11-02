"""Shared fixtures for the recipe plugin tests (runs: :mod:`recipe_test_support`)."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from untaped.sdk.testing import Install
from untaped_recipe.plugin import RecipePlugin


@pytest.fixture(autouse=True)
def stub_lock_freshness(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the real `uv lock --check` probe in unit tests.

    The suite's packs write placeholder uv.lock files (existence-only
    convention); a real probe would need uv resolution per check. Tests that
    exercise the probe re-patch `pack_files.check_lock` explicitly.
    """
    monkeypatch.setattr(
        "untaped_recipe.infrastructure.pack_files.check_lock",
        lambda project_root: None,
    )


@pytest.fixture
def recipe_run(install: Callable[..., Install]) -> Iterator[None]:
    """Run the test under recipe's run, as a command does: a backup locks under the home."""
    with install(RecipePlugin).running():
        yield
