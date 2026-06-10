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
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)
    reset_config_registry_for_tests()
    get_settings.cache_clear()
    yield cfg
    os.environ.pop("UNTAPED_PROFILE", None)
    reset_config_registry_for_tests()
    get_settings.cache_clear()


def _write_profiles(cfg: Path) -> None:
    cfg.write_text(
        "profiles:\n"
        "  default:\n"
        "    demo:\n"
        "      endpoint: https://default.example\n"
        "  stage:\n"
        "    demo:\n"
        "      endpoint: https://stage.example\n"
    )


def test_plugin_context_exposes_registered_section(_isolated_config: Path) -> None:
    register_profile_settings("demo", DemoSettings)
    _write_profiles(_isolated_config)

    ctx = plugin_context()

    assert isinstance(ctx, PluginContext)
    assert ctx.section("demo", DemoSettings).endpoint == "https://default.example"


def test_plugin_context_resolves_profile_without_leaking_state(
    _isolated_config: Path,
) -> None:
    register_profile_settings("demo", DemoSettings)
    _write_profiles(_isolated_config)

    ctx = plugin_context(profile="stage")

    assert ctx.section("demo", DemoSettings).endpoint == "https://stage.example"
    # The override must not leak into ambient process state.
    assert "UNTAPED_PROFILE" not in os.environ
    assert get_settings().demo.endpoint == "https://default.example"  # type: ignore[attr-defined]


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
