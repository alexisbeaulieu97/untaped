"""Shared fixtures for the recipe plugin tests (runs: :mod:`recipe_test_support`)."""

from __future__ import annotations

import pytest


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
