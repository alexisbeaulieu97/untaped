"""Ansible plugin for the unified ``untaped`` shell.

GitHub behavior is consumed through the closed API in
:mod:`untaped_github.api`.
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import PluginSpec, SkillAsset, delete_migration, old_dirs
from untaped_ansible.doctor import DOCTOR_CHECKS
from untaped_ansible.settings import AnsibleSettings, AnsibleState

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the ansible cyclopts app."""
    from untaped_ansible.cli import app  # noqa: PLC0415

    return app


def _git_cache_10x() -> Sequence[Path]:
    """10.x's git cache, with any custom root config.yml still names (9.x's key included)."""
    roots = old_dirs("~/.untaped/ansible-cache", "ansible", "cache_dir")
    return [*roots, *old_dirs("~/.untaped/ansible-cache", "ansible", "repo_cache_path")]


def _git_cache_9x() -> Sequence[Path]:
    return [Path("~/.untaped/ansible-repositories").expanduser()]


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
    migrations=(
        delete_migration(
            "ansible.cache",
            "10.x git cache",
            _git_cache_10x,
            detail="the index keeps each ref's commit: the next refresh fetches only what changed",
        ),
        delete_migration(
            "ansible.repositories", "9.x git cache", _git_cache_9x, detail="9.x layout"
        ),
    ),
)
