"""The recipe plugin passes `untaped lint`: the command, message and structure rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped_lint.testing import assert_clean

pytestmark = pytest.mark.usefixtures("fresh_composition")


def test_recipe_passes_lint() -> None:
    assert_clean("recipe", tests_dir=Path(__file__).resolve().parents[1])
