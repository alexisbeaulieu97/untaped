"""The recipe settings no longer read their retired keys."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from untaped.sdk import home, settings_of
from untaped.sdk.testing import install
from untaped_recipe.plugin import RecipePlugin
from untaped_recipe.settings import RecipeSettings


def test_the_retired_library_root_is_not_read(capsys: pytest.CaptureFixture[str]) -> None:
    installed = install(RecipePlugin)  # before the config it reads: install writes its sections
    config = home().expanduser() / "config.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump({"format_version": 2, "recipe": {"library_root": "/l"}}))

    with installed.running():
        settings = settings_of(RecipeSettings)

    assert settings.library_dir == RecipeSettings().library_dir != Path("/l")
    assert "library_root" not in capsys.readouterr().err
