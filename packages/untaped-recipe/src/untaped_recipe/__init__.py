"""Recipe plugin: apply reusable recipe packs to plain directories.

Exposes a static ``SPEC: PluginSpec`` plus a nullary :func:`build_app`
factory. Importing this package never constructs the CLI tree;
:func:`build_app` imports it on demand at mount time.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import PluginSpec, SkillAsset, executable_check
from untaped_recipe.settings import RecipeSettings

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["SPEC", "build_app", "provider"]


def build_app() -> App:
    """Nullary factory returning the recipe cyclopts app."""
    from untaped_recipe.cli import app  # noqa: PLC0415

    return app


SPEC = PluginSpec(
    name="recipe",
    app_factory=build_app,
    help="Apply reusable local recipes to plain directories.",
    settings=RecipeSettings,
    skills=(
        SkillAsset(
            name="untaped-recipe",
            source=Path(str(files("untaped_recipe").joinpath("skills", "untaped-recipe"))),
            description=(
                "Applies reusable file recipes (templated files, YAML edits, copies, removals) "
                "across many directories or repos through the `untaped recipe` command, with a "
                "preview, backups and a CI drift check, and authors and tests recipe packs. Use "
                "when the user mentions recipes or recipe packs, codemods, the same file change "
                "across many repos, or drift checks."
            ),
        ),
    ),
    doctor_checks=(
        executable_check("recipe.uv", "uv", purpose="recipe hooks and packs"),
        executable_check("recipe.git", "git", purpose="installing packs from git"),
    ),
)


def provider() -> PluginSpec:
    """Entry-point provider: the ``untaped.plugins`` entry point names this."""
    return SPEC
