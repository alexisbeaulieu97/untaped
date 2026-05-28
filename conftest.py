"""Shared pytest fixtures for the in-repo plugin bridge."""

from __future__ import annotations

from collections.abc import Iterator
from importlib import import_module

import pytest

from untaped.plugins import PluginRegistry, set_current_registry
from untaped.settings import (
    get_settings,
    register_profile_settings,
    register_state_settings,
    reset_config_registry_for_tests,
)


@pytest.fixture(autouse=True)
def _register_legacy_plugin_settings_for_tests() -> Iterator[None]:
    """Keep bridge-step tests focused while package settings move behind plugins."""
    # Bridge-step only: these dynamic imports preserve legacy tests while plugin
    # packages still live in the monorepo. Delete this fixture when they move out.
    awx_config = import_module("untaped_awx.infrastructure").AwxConfig
    github_settings = import_module("untaped_github.settings").GithubSettings
    workspace_settings = import_module("untaped_workspace.settings").WorkspaceSettings
    workspace_state = import_module("untaped_workspace.settings").WorkspaceState

    reset_config_registry_for_tests()
    set_current_registry(PluginRegistry())
    register_profile_settings("awx", awx_config)
    register_profile_settings("github", github_settings)
    register_profile_settings("workspace", workspace_settings)
    register_state_settings("workspace", workspace_state)
    get_settings.cache_clear()
    yield
    reset_config_registry_for_tests()
    set_current_registry(PluginRegistry())
    get_settings.cache_clear()
