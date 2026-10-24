"""Root management commands for the unified ``untaped`` shell."""

from __future__ import annotations

from untaped.core.commands.agents import build_root_agents_app
from untaped.core.commands.auth import build_root_auth_app
from untaped.core.commands.doctor import build_root_doctor_app
from untaped.core.commands.setup import build_root_setup_app
from untaped.core.commands.skills import build_root_skills_app

__all__ = [
    "build_root_agents_app",
    "build_root_auth_app",
    "build_root_doctor_app",
    "build_root_setup_app",
    "build_root_skills_app",
]
