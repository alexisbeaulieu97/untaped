"""Tests for plugin-facing helpers in ``untaped.testing``."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped.plugin_context import plugin_context
from untaped.plugins import PluginManifest
from untaped.settings import get_settings, reset_config_registry_for_tests
from untaped.testing import register_plugin_for_tests


class DemoSettings(BaseModel):
    endpoint: str = "https://default.example"


class _DemoPlugin:
    id = "demo"
    untaped_api_version = 3

    def manifest(self) -> PluginManifest:
        return PluginManifest(profile_settings={"demo": DemoSettings})


class _BrokenPlugin:
    id = "broken"
    untaped_api_version = 3

    def manifest(self) -> PluginManifest:
        raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "config.yml"))
    reset_config_registry_for_tests()
    get_settings.cache_clear()
    yield
    reset_config_registry_for_tests()
    get_settings.cache_clear()


def test_register_plugin_for_tests_makes_sections_resolvable() -> None:
    register_plugin_for_tests(_DemoPlugin())

    section = plugin_context().section("demo", DemoSettings)

    assert section.endpoint == "https://default.example"


def test_register_plugin_for_tests_fails_loudly_on_broken_plugin() -> None:
    with pytest.raises(AssertionError, match="boom"):
        register_plugin_for_tests(_BrokenPlugin())
