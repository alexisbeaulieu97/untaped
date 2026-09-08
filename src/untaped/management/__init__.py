"""Root management commands for the unified ``untaped`` shell."""

from __future__ import annotations

from untaped.management.capabilities import build_root_capabilities_app
from untaped.management.config import build_root_config_app
from untaped.management.doctor import build_root_doctor_app
from untaped.management.profile import build_root_profile_app
from untaped.management.skills import build_root_skills_app

__all__ = [
    "build_root_capabilities_app",
    "build_root_config_app",
    "build_root_doctor_app",
    "build_root_profile_app",
    "build_root_skills_app",
]
