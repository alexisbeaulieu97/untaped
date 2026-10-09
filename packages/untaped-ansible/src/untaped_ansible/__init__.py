"""Ansible plugin for the unified ``untaped`` shell.

GitHub behavior is consumed through the closed API in
:mod:`untaped_github.api`.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import PluginSpec, SkillAsset
from untaped_ansible.doctor import DOCTOR_CHECKS
from untaped_ansible.settings import AnsibleSettings, AnsibleState

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["SPEC", "build_app", "provider"]


def build_app() -> App:
    """Nullary factory returning the ansible cyclopts app."""
    from untaped_ansible.cli import app  # noqa: PLC0415

    return app


SPEC = PluginSpec(
    name="ansible",
    app_factory=build_app,
    help="Analyze Ansible dependency graphs.",
    settings=AnsibleSettings,
    state=AnsibleState,
    skills=(
        SkillAsset(
            name="untaped-ansible",
            source=Path(str(files("untaped_ansible").joinpath("skills", "untaped-ansible"))),
            description=(
                "Maps Ansible role and project dependencies across GitHub repositories through "
                "the `untaped ansible` command (what a role depends on, what depends on it, which"
                " projects reach a repo, and dependency graphs). Use when the user asks who uses "
                "a role, the impact of changing one, or about requirements.yml or meta/main.yml "
                "dependencies."
            ),
        ),
    ),
    doctor_checks=DOCTOR_CHECKS,
)


def provider() -> PluginSpec:
    """Entry-point provider: the ``untaped.plugins`` entry point names this."""
    return SPEC
