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
from untaped.capability_api import CapabilitySpec, SkillAsset

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the github cyclopts app."""
    from untaped.capabilities.github.cli import app  # noqa: PLC0415

    return app


SPEC = CapabilitySpec(
    name="github",
    app_factory=build_app,
    config_section="github",
    profile_model=GithubSettings,
    skills=(
        SkillAsset(
            name="untaped-github",
            source=Path(
                str(files("untaped.capabilities.github").joinpath("skills", "untaped-github"))
            ),
            description="Use the built-in `untaped github` capability for GitHub workflows.",
        ),
    ),
    doctor_checks=(),
)
