"""Tests for the per-invocation tool execution context."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped.app_context import AppContext, app_context
from untaped.errors import ConfigError
from untaped.settings import (
    HttpSettings,
    get_settings,
    register_profile_settings,
    reset_config_registry_for_tests,
)
from untaped.theme import BUILTIN_THEMES
from untaped.ui import ui_context


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


def test_app_context_exposes_registered_section(_isolated_config: Path) -> None:
    register_profile_settings("demo", DemoSettings)
    _isolated_config.write_text(
        "profiles:\n  default:\n    demo:\n      endpoint: https://configured.example\n"
    )

    ctx = app_context()

    assert isinstance(ctx, AppContext)
    assert ctx.section("demo", DemoSettings).endpoint == "https://configured.example"


def test_app_context_section_rejects_unregistered_sections(_isolated_config: Path) -> None:
    ctx = app_context()

    with pytest.raises(ConfigError, match="not registered"):
        ctx.section("nope", DemoSettings)


def test_app_context_exposes_http_settings(_isolated_config: Path) -> None:
    ctx = app_context()

    assert isinstance(ctx.http, HttpSettings)
    assert ctx.http.verify_ssl is True


def test_ui_uses_settings_snapshot_not_later_cache_state(_isolated_config: Path) -> None:
    """``ctx.ui()`` builds from the context's frozen settings snapshot, so a
    settings-cache invalidation after the context exists must not change the
    theme it renders with."""
    _isolated_config.write_text("profiles:\n  default:\n    ui:\n      theme: classic\n")
    get_settings.cache_clear()
    ctx = app_context()
    snapshot_theme = ctx.ui().theme

    # Change the theme and drop the cache after the context was built.
    _isolated_config.write_text("profiles:\n  default:\n    ui:\n      theme: default\n")
    get_settings.cache_clear()

    # A fresh, re-reading context observes the change...
    assert ui_context().theme != snapshot_theme
    # ...but the snapshot-bound context stays pinned to its resolved theme.
    assert ctx.ui().theme == snapshot_theme


def test_ui_degrades_unknown_theme_per_strict_flag(_isolated_config: Path) -> None:
    """``ctx.ui()`` shares one degrade policy with ``ui_context``: an unknown
    theme name falls back to the default preset when ``strict=False`` and raises
    when ``strict=True``."""
    _isolated_config.write_text("profiles:\n  default:\n    ui:\n      theme: nonexistent\n")
    get_settings.cache_clear()
    ctx = app_context()

    assert ctx.ui(strict=False).theme == BUILTIN_THEMES["default"]
    with pytest.raises(ConfigError, match="unknown UI theme"):
        ctx.ui(strict=True)
