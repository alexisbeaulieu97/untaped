"""Tests for the per-invocation plugin execution context."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped.errors import ConfigError
from untaped.plugin_context import PluginContext, plugin_context
from untaped.settings import (
    HttpSettings,
    get_settings,
    register_profile_settings,
    reset_config_registry_for_tests,
)


class DemoSettings(BaseModel):
    endpoint: str = "https://default.example"


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    reset_config_registry_for_tests()
    get_settings.cache_clear()
    yield cfg
    reset_config_registry_for_tests()
    get_settings.cache_clear()


def test_plugin_context_exposes_registered_section(_isolated_config: Path) -> None:
    register_profile_settings("demo", DemoSettings)
    _isolated_config.write_text("demo:\n  endpoint: https://configured.example\n")

    ctx = plugin_context()

    assert isinstance(ctx, PluginContext)
    assert ctx.section("demo", DemoSettings).endpoint == "https://configured.example"


def test_plugin_context_accepts_deprecated_profile_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scope selection happens before dispatch via plugin root options, but
    released v3-era plugins still pass ``plugin_context(profile)``; the
    deprecated override must resolve settings without leaking into ambient
    process state (release-smoke regression, PR #273)."""
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)

    ctx = plugin_context(profile="stage")

    assert isinstance(ctx, PluginContext)
    assert "UNTAPED_PROFILE" not in os.environ


def test_plugin_context_section_rejects_unregistered_sections(
    _isolated_config: Path,
) -> None:
    ctx = plugin_context()

    with pytest.raises(ConfigError, match="not registered"):
        ctx.section("nope", DemoSettings)


def test_plugin_context_exposes_http_settings(_isolated_config: Path) -> None:
    ctx = plugin_context()

    assert isinstance(ctx.http, HttpSettings)
    assert ctx.http.verify_ssl is True
