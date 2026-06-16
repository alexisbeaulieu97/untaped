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
