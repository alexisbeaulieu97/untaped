"""The recipe settings read their renamed keys."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from untaped.sdk import get_config_section
from untaped_recipe.settings import RecipeSettings


def test_library_root_is_read_as_library_dir(capsys: pytest.CaptureFixture[str]) -> None:
    config = Path(os.environ["UNTAPED_CONFIG"])
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump({"profiles": {"default": {"recipe": {"library_root": "/l"}}}}))

    assert get_config_section("recipe", RecipeSettings).library_dir == Path("/l")
    assert (
        "warning: recipe.library_root is deprecated and will be removed in the next major "
        "release; use recipe.library_dir"
    ) in capsys.readouterr().err
