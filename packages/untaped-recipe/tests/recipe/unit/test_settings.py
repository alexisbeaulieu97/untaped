"""The recipe settings no longer read their retired keys."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from untaped.sdk import get_config_section, home
from untaped_recipe.settings import RecipeSettings


def test_the_retired_library_root_is_not_read(capsys: pytest.CaptureFixture[str]) -> None:
    config = home().expanduser() / "config.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump({"profiles": {"default": {"recipe": {"library_root": "/l"}}}}))

    settings = get_config_section("recipe", RecipeSettings)

    assert settings.library_dir == RecipeSettings().library_dir != Path("/l")
    assert "library_root" not in capsys.readouterr().err
