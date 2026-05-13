"""Unit tests for ``GithubConfig.from_settings``.

Pins the field-by-field bridge between ``untaped_core.Settings.github``
and the package-local ``GithubConfig``. A new field on either side
without its sibling now surfaces as a test failure, not a silent runtime
drop or duplicated CLI-site copy.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr
from untaped_core import Settings
from untaped_core.settings import GithubSettings, get_settings
from untaped_github.infrastructure import GithubConfig


@pytest.fixture(autouse=True)
def _reset_settings_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", "/nonexistent/path.yml")
    get_settings.cache_clear()


def test_from_settings_copies_every_field_from_defaults() -> None:
    settings = Settings()
    config = GithubConfig.from_settings(settings)
    assert config.base_url == settings.github.base_url
    assert config.token == settings.github.token


def test_from_settings_copies_non_default_values() -> None:
    settings = Settings(
        github=GithubSettings(
            base_url="https://ghe.example.com/api/v3",
            token=SecretStr("ghp_xxx"),
        )
    )
    config = GithubConfig.from_settings(settings)
    assert config.base_url == "https://ghe.example.com/api/v3"
    assert config.token is not None
    assert config.token.get_secret_value() == "ghp_xxx"


def test_from_settings_returns_githubconfig_instance() -> None:
    config = GithubConfig.from_settings(Settings())
    assert isinstance(config, GithubConfig)


def test_from_settings_field_set_matches_githubsettings() -> None:
    """Pin the field inventory so an unbalanced add to either side
    surfaces here rather than as a silent runtime drop."""
    settings_fields = set(GithubSettings.model_fields.keys())
    config_fields = set(GithubConfig.model_fields.keys())
    assert settings_fields == config_fields, {
        "in_settings_only": sorted(settings_fields - config_fields),
        "in_config_only": sorted(config_fields - settings_fields),
    }


def test_no_inline_githubconfig_constructor_in_cli() -> None:
    """``cli/commands.py`` and ``cli/search_commands.py`` used to copy
    ``base_url`` / ``token`` inline. After the refactor, the only place
    that constructs ``GithubConfig`` from settings is the new
    ``cli/_client.py`` module, and it goes through ``from_settings``.

    The match is broad on purpose: any ``GithubConfig(`` call in CLI
    files trips the assertion regardless of kwarg order, so the
    backstop survives a future field reshuffle that would let
    ``GithubConfig(token=..., base_url=...)`` slip a narrower check."""
    from pathlib import Path

    pkg = Path(__file__).resolve().parents[2] / "src" / "untaped_github" / "cli"
    for name in ("commands.py", "search_commands.py"):
        text = (pkg / name).read_text()
        assert "GithubConfig(" not in text, (
            f"{name} still constructs GithubConfig inline; route through "
            f"GithubConfig.from_settings via the _client helper"
        )
