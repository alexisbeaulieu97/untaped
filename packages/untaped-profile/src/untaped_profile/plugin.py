"""Untaped plugin registration for profile management commands."""

from __future__ import annotations

from untaped.plugins import PluginRegistry
from untaped_profile import app


class ProfilePlugin:
    id = "profile"

    def register(self, registry: PluginRegistry) -> None:
        registry.add_cli("profile", app)


plugin = ProfilePlugin()
