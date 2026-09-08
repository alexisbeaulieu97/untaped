"""Shared pytest fixtures for the untaped SDK tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from untaped.settings import get_settings, reset_config_registry_for_tests


@pytest.fixture(autouse=True)
def _isolate_config_registry_for_tests() -> Iterator[None]:
    """Reset the registered config sections around each test."""
    reset_config_registry_for_tests()
    get_settings.cache_clear()
    yield
    reset_config_registry_for_tests()
    get_settings.cache_clear()
