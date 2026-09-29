"""GitHub capability for the unified ``untaped`` shell.

The nullary :func:`build_app` factory is imported on demand when the root
mounts the capability. GitHub-owned integration types used by Ansible live in
:mod:`untaped.capabilities.github.ansible`.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.capabilities.github.settings import GithubSettings
from untaped.capability_api import (
    CapabilitySpec,
    SkillAsset,
    connection_check,
    executable_check,
    online_check,
)

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the github cyclopts app."""
    from untaped.capabilities.github.cli import app  # noqa: PLC0415

    return app


def _probe_api() -> str:
    """``doctor --online``: authenticate against GitHub (imports the CLI lazily)."""
    from untaped.capabilities.github.cli.doctor import probe_api  # noqa: PLC0415

    return probe_api()


SPEC = CapabilitySpec(
    name="github",
    app_factory=build_app,
    help="Inspect and search GitHub from the authenticated user's account.",
    config_section="github",
    profile_model=GithubSettings,
    skills=(
        SkillAsset(
            name="untaped-github",
            source=Path(
                str(files("untaped.capabilities.github").joinpath("skills", "untaped-github"))
            ),
            description=(
                "Use the `untaped github` command to query GitHub or GitHub Enterprise (list "
                "org and team repositories, search repositories, code, issues and users, and "
                "sweep local clones of many repositories with grep-style questions). Use when "
                "the user mentions GitHub, GHE, repos, orgs, teams, code search, issue search, "
                "which repos use something, or a codebase-wide sweep."
            ),
        ),
    ),
    doctor_checks=(
        connection_check("github.connection", section="github"),
        online_check("github.api", section="github", probe=_probe_api),
        executable_check("github.git", "git", purpose="`untaped github sweep`"),
    ),
)
