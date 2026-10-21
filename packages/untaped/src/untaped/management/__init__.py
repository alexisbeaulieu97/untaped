"""Root management commands for the unified ``untaped`` shell."""

from __future__ import annotations

from untaped.management.agents import build_root_agents_app
from untaped.management.auth import build_root_auth_app
from untaped.management.config import build_root_config_app
from untaped.management.doctor import build_root_doctor_app
from untaped.management.plugins import build_root_plugin_app
from untaped.management.profile import build_root_profile_app
from untaped.management.project import build_root_project_app
from untaped.management.setup import build_root_setup_app
from untaped.management.skills import build_root_skills_app

__all__ = [
    "build_root_agents_app",
    "build_root_auth_app",
    "build_root_config_app",
    "build_root_doctor_app",
    "build_root_plugin_app",
    "build_root_profile_app",
    "build_root_project_app",
    "build_root_setup_app",
    "build_root_skills_app",
]
