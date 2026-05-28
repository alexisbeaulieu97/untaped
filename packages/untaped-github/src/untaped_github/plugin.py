"""Untaped plugin registration for the GitHub domain."""

from __future__ import annotations

from untaped.plugins import PluginRegistry
from untaped_github import app
from untaped_github.settings import GithubSettings


class GithubPlugin:
    id = "github"

    def register(self, registry: PluginRegistry) -> None:
        registry.add_profile_settings("github", GithubSettings)
        registry.add_cli("github", app)


plugin = GithubPlugin()
