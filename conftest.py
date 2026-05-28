"""Shared pytest fixtures for the in-repo plugin bridge."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from untaped_awx.infrastructure import AwxConfig
from untaped_github.settings import GithubSettings
from untaped_workspace.settings import WorkspaceSettings

from untaped.settings import (
    get_settings,
    register_profile_settings,
    register_state_settings,
    reset_config_registry_for_tests,
)


@pytest.fixture(autouse=True)
def _register_legacy_plugin_settings_for_tests() -> Iterator[None]:
    """Keep pre-plugin tests focused while package settings move behind plugins."""
    reset_config_registry_for_tests()
    register_profile_settings("awx", AwxConfig)
    register_profile_settings("github", GithubSettings)
    register_profile_settings("workspace", WorkspaceSettings)
    register_state_settings("workspace", WorkspaceSettings)
    get_settings.cache_clear()
    yield
    reset_config_registry_for_tests()
    get_settings.cache_clear()
