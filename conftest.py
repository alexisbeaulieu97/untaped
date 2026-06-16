"""Shared pytest fixtures for the untaped SDK tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from untaped.identity import reset_tool_command
from untaped.settings import get_settings, reset_config_registry_for_tests


@pytest.fixture(autouse=True)
def _isolate_config_registry_for_tests() -> Iterator[None]:
    """Reset the registered config sections and tool identity around each test."""
    reset_config_registry_for_tests()
    reset_tool_command()
    get_settings.cache_clear()
    yield
    reset_config_registry_for_tests()
    reset_tool_command()
    get_settings.cache_clear()
