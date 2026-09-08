"""Ansible capability for the unified ``untaped`` shell.

GitHub behavior is consumed through the closed API in
:mod:`untaped.capabilities.github.ansible`.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.capabilities.ansible.settings import AnsibleSettings, AnsibleState
from untaped.capability_api import CapabilitySpec, SkillAsset

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the ansible cyclopts app."""
    from untaped.capabilities.ansible.cli import app  # noqa: PLC0415

    return app


SPEC = CapabilitySpec(
    name="ansible",
    app_factory=build_app,
    config_section="ansible",
    profile_model=AnsibleSettings,
    state_model=AnsibleState,
    skills=(
        SkillAsset(
            name="untaped-ansible",
            source=Path(
                str(files("untaped.capabilities.ansible").joinpath("skills", "untaped-ansible"))
            ),
            description="Use the built-in `untaped ansible` capability for Ansible analysis.",
        ),
    ),
    doctor_checks=(),
)
