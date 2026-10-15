"""Recipe plugin: apply reusable recipe packs to plain directories.

Exposes a static ``SPEC: PluginSpec`` plus a nullary :func:`build_app`
factory. Importing this package never constructs the CLI tree;
:func:`build_app` imports it on demand at mount time.
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import (
    PluginContext,
    PluginSpec,
    SkillAsset,
    executable_check,
    move_migration,
    plugin_dir,
)
from untaped_recipe.settings import RecipeSettings

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the recipe cyclopts app."""
    from untaped_recipe.cli import app  # noqa: PLC0415

    return app


def _library_moves(ctx: PluginContext) -> Sequence[tuple[Path, Path]]:
    """10.x's library into ``plugin_dir`` while ``recipe.library_dir`` keeps its default.

    The library holds only paths relative to itself (``packs.toml``, the pack
    copies, backups), and uv rebuilds a moved pack's ``.venv`` on its next
    hook run, so the directory moves whole. A profile pointing
    ``library_dir`` anywhere else keeps what it names; settings that don't
    validate move nothing.
    """
    settings = ctx.settings
    if not isinstance(settings, RecipeSettings):
        return []
    if settings.library_dir != RecipeSettings.model_fields["library_dir"].default:
        return []
    return [(Path("~/.untaped/untaped-recipes").expanduser(), plugin_dir(SPEC) / "library")]


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
    migrations=(
        move_migration(
            "recipe.library",
            "10.x recipe library",
            _library_moves,
            detail="installed packs and backups, kept as they are",
        ),
    ),
)
