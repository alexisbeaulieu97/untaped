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
    PluginSpec,
    SkillAsset,
    executable_check,
    move_migration,
    setting_guard,
)
from untaped_recipe.settings import RecipeSettings

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the recipe cyclopts app."""
    from untaped_recipe.cli import app  # noqa: PLC0415

    return app


def _library_moves() -> Sequence[tuple[Path, Path]]:
    """10.x's library into ``plugin_dir``, where ``recipe.library_dir`` now defaults.

    The library holds only paths relative to itself (``packs.toml``, the pack
    copies, backups), and uv rebuilds a moved pack's ``.venv`` on its next
    hook run, so the directory moves whole.
    """
    new: Path = RecipeSettings.model_fields["library_dir"].default
    return [(Path("~/.untaped/untaped-recipes").expanduser(), new.expanduser())]


def _empty_library(path: Path) -> bool:
    """Whether the library at ``path`` holds no pack and no backup (a command made it, no more)."""
    from untaped_recipe.infrastructure.pack_store import holds_nothing  # noqa: PLC0415

    return holds_nothing(path)


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
            guard=setting_guard("recipe", "library_dir"),
            replaceable=_empty_library,
        ),
    ),
)
