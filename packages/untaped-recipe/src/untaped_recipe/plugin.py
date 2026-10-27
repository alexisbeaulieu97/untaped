"""The ``recipe`` plugin class: what the plugin has, each under its own name.

The ``untaped.plugins`` entry point names :class:`RecipePlugin`, in this leaf
module: the class imports every command.
"""

from __future__ import annotations

from untaped.sdk import ExecutableCheck, Plugin, StubSkill
from untaped_recipe.commands.apply import Apply
from untaped_recipe.commands.backups import BackupsGroup
from untaped_recipe.commands.hooks import HooksGroup
from untaped_recipe.commands.library import Edit, Get, Init, List, Validate
from untaped_recipe.commands.packs import PacksGroup
from untaped_recipe.commands.test import Test
from untaped_recipe.settings import LIBRARY, RecipeSettings


class RecipePlugin(Plugin):
    """Apply reusable local recipes to plain directories."""

    settings = RecipeSettings
    commands = (Apply, List, Get, Edit, Init, Validate, Test, PacksGroup, HooksGroup, BackupsGroup)
    library = LIBRARY
    uv = ExecutableCheck("uv", purpose="recipe hooks and packs")
    git = ExecutableCheck("git", purpose="installing packs from git")
    skill = StubSkill(
        "Recipe packs and the file changes they apply across many directories or "
        "repos: templated files, YAML edits, copies and removals, with previews, "
        "backups and drift checks, plus authoring and testing packs and hooks. Use "
        "when the work concerns recipes or recipe packs, codemods, the same file "
        "change across many repos, or drift checks."
    )


__all__ = ["RecipePlugin"]
