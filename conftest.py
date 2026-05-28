"""Shared pytest fixtures for core tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from untaped.plugins import PluginRegistry, set_current_registry
from untaped.settings import get_settings, reset_config_registry_for_tests


@pytest.fixture(autouse=True)
def _isolate_plugin_registry_for_tests() -> Iterator[None]:
    """Reset plugin/config registries around each test."""
    reset_config_registry_for_tests()
    set_current_registry(PluginRegistry())
    get_settings.cache_clear()
    yield
    reset_config_registry_for_tests()
    set_current_registry(PluginRegistry())
    get_settings.cache_clear()
