"""Unit tests for ``AwxConfig.from_settings``.

Pins the field-by-field bridge between ``untaped_core.Settings.awx`` and
the package-local ``AwxConfig`` so a new field added to one side without
the other surfaces as a test failure, not as a silent runtime drop.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr
from untaped_awx.infrastructure import AwxConfig
from untaped_core import Settings
from untaped_core.settings import AwxSettings, get_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings() reads YAML by default — give every test a clean env."""
    monkeypatch.setenv("UNTAPED_CONFIG", "/nonexistent/path.yml")
    get_settings.cache_clear()


def test_from_settings_copies_every_field_from_defaults() -> None:
    """A default ``Settings()`` round-trips into a default ``AwxConfig`` —
    each field on the bridge must read the same value from the source."""
    settings = Settings()
    config = AwxConfig.from_settings(settings)
    assert config.base_url == settings.awx.base_url
    assert config.token == settings.awx.token
    assert config.api_prefix == settings.awx.api_prefix
    assert config.default_organization == settings.awx.default_organization
    assert config.page_size == settings.awx.page_size


def test_from_settings_copies_non_default_values() -> None:
    """Construct a non-default ``Settings`` and verify every field
    propagates. Catches a typo on either side of the bridge."""
    settings = Settings(
        awx=AwxSettings(
            base_url="https://aap.example.com",
            token=SecretStr("a-token"),
            api_prefix="/api/v2/",
            default_organization="Default",
            page_size=100,
        )
    )
    config = AwxConfig.from_settings(settings)
    assert config.base_url == "https://aap.example.com"
    assert config.token is not None
    assert config.token.get_secret_value() == "a-token"
    assert config.api_prefix == "/api/v2/"
    assert config.default_organization == "Default"
    assert config.page_size == 100


def test_from_settings_returns_awxconfig_instance() -> None:
    """Bridge must return the package-local type — not the cross-cutting
    ``AwxSettings`` — so adapters keep their narrow import surface."""
    config = AwxConfig.from_settings(Settings())
    assert isinstance(config, AwxConfig)


def test_from_settings_field_set_matches_awxsettings() -> None:
    """If a new field is added to ``AwxSettings`` but not to ``AwxConfig``
    (or vice-versa), every test above could still pass. Pin the field
    inventory here so adding a setting on one side without the bridge
    fails loudly."""
    settings_fields = set(AwxSettings.model_fields.keys())
    config_fields = set(AwxConfig.model_fields.keys())
    assert settings_fields == config_fields, {
        "in_settings_only": sorted(settings_fields - config_fields),
        "in_config_only": sorted(config_fields - settings_fields),
    }
