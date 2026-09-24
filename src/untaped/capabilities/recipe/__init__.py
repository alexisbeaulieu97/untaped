"""Recipe capability: apply reusable recipe packs to plain directories.

Exposes a static ``SPEC: CapabilitySpec`` plus a nullary :func:`build_app`
factory. Importing this package never constructs the CLI tree;
:func:`build_app` imports it on demand at mount time.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.capabilities.recipe.settings import RecipeSettings
from untaped.capability_api import CapabilitySpec, SkillAsset, executable_check

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the recipe cyclopts app."""
    from untaped.capabilities.recipe.cli import app  # noqa: PLC0415

    return app


SPEC = CapabilitySpec(
    name="recipe",
    app_factory=build_app,
    help="Apply reusable local recipes to plain directories.",
    config_section="recipe",
    profile_model=RecipeSettings,
    skills=(
        SkillAsset(
            name="untaped-recipe",
            source=Path(
                str(files("untaped.capabilities.recipe").joinpath("skills", "untaped-recipe"))
            ),
            description=(
                "Use the untaped recipe capability to apply local recipe packs across directories."
            ),
        ),
    ),
    doctor_checks=(
        executable_check("recipe.uv", "uv", purpose="recipe hooks and packs"),
        executable_check("recipe.git", "git", purpose="installing packs from git"),
    ),
)
