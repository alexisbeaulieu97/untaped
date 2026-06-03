"""Shared unit-test fixtures for plugin CLI tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from untaped.plugins import PluginRegistry, set_current_registry
from untaped.settings import get_settings, reset_config_registry_for_tests


@pytest.fixture
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    reset_config_registry_for_tests()
    set_current_registry(PluginRegistry())
    get_settings.cache_clear()
    yield cfg
    reset_config_registry_for_tests()
    set_current_registry(PluginRegistry())
    get_settings.cache_clear()
