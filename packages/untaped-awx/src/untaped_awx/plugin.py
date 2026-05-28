"""Untaped plugin registration for the AWX/AAP domain."""

from __future__ import annotations

from untaped.plugins import PluginRegistry
from untaped_awx import app
from untaped_awx.infrastructure import AwxConfig


class AwxPlugin:
    id = "awx"

    def register(self, registry: PluginRegistry) -> None:
        registry.add_profile_settings("awx", AwxConfig)
        registry.add_cli("awx", app)


plugin = AwxPlugin()
