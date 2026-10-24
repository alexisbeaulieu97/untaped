"""Root management commands for the unified ``untaped`` shell."""

from __future__ import annotations

from untaped.core.commands.agents import build_root_agents_app
from untaped.core.commands.setup import build_root_setup_app

__all__ = [
    "build_root_agents_app",
    "build_root_setup_app",
]
