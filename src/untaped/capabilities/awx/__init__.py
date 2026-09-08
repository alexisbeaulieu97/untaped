"""AWX capability for the unified ``untaped`` shell."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.capabilities.awx.settings import AwxSettings
from untaped.capability_api import CapabilitySpec, SkillAsset

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the awx cyclopts app."""
    from untaped.capabilities.awx.cli import app  # noqa: PLC0415

    return app


SPEC = CapabilitySpec(
    name="awx",
    app_factory=build_app,
    config_section="awx",
    profile_model=AwxSettings,
    skills=(
        SkillAsset(
            name="untaped-awx",
            source=Path(str(files("untaped.capabilities.awx").joinpath("skills", "untaped-awx"))),
            description="Use the built-in `untaped awx` capability for AWX/AAP workflows.",
        ),
    ),
    doctor_checks=(),
)
