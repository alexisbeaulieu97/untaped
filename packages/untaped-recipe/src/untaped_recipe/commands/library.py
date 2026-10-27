"""``untaped recipe list|get|edit|init|validate``: the recipes of the installed packs."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from pydantic import Field

from untaped.sdk import Document, Failed, Output, Positional, hint, run_editor
from untaped_recipe.application.check_pack import check_library, check_ref
from untaped_recipe.commands._library import NO_LOCK_NOTE, find_recipe, pack_child
from untaped_recipe.commands._recipe import RecipeCommand, recipe_errors
from untaped_recipe.commands.detail import recipe_record
from untaped_recipe.domain.recipe import RecipeFile
from untaped_recipe.infrastructure import pack_scaffold
from untaped_recipe.infrastructure.pack_inspector import PackInspector
from untaped_recipe.records import Check, Recipe

#: How a library recipe ref reads in help.
LIBRARY_REF = "Recipe name or PACK/RECIPE reference."

#: How ``--lock`` reads in help, on every scaffolding command.
LOCK = "Refresh uv.lock after scaffolding (--no-lock skips it)."


class List(RecipeCommand):
    """List installed recipes.

    Recipes come from installed packs, so the list is empty until one is
    installed (`untaped recipe packs add`). A pack whose `pyproject.toml`
    doesn't parse is skipped with a warning, here and when resolving refs,
    unless named explicitly.
    """

    output: Output[Recipe]

    def run(self) -> Iterator[Recipe]:
        with recipe_errors():
            installed = self.installed()
            rows = [
                recipe_record(pack, name, detail=False)
                for pack in installed
                for name in sorted(pack.manifest.recipes)
            ]
        if not installed:
            self.empty_hint()
        return iter(rows)


class Get(RecipeCommand):
    """Show an installed recipe: its inputs, steps and hooks."""

    ref: Annotated[Positional[str], Document(RecipeFile)] = Field(description=LIBRARY_REF)
    output: Output[Recipe]

    def run(self) -> Recipe:
        with recipe_errors():
            pack, name, _entry = find_recipe(self.library(), self.ref, verb="get")
            return recipe_record(pack, name, detail=True)


class Edit(RecipeCommand):
    """Open an installed recipe file in $VISUAL or $EDITOR."""

    ref: Annotated[Positional[str], Document(RecipeFile)] = Field(description=LIBRARY_REF)

    def run(self) -> None:
        with recipe_errors():
            pack, _name, recipe = find_recipe(self.library(), self.ref, verb="edit")
            run_editor(pack.root / recipe.path)


class Init(RecipeCommand):
    """Scaffold a recipe, with a starter golden test case, inside a pack.

    A `./path/RECIPE` ref targets the pack at `./path`. It locks the pack like
    `packs init`, and prints the new recipe file's path.
    """

    ref: Positional[str] = Field(description="PACK/RECIPE reference (or ./path/RECIPE).")
    lock: bool = Field(True, description=LOCK)

    def run(self) -> str:
        with recipe_errors():
            if "/" not in self.ref:
                raise ValueError(
                    f"recipe refs must use <pack>/<recipe>\n{hint(f'recipe packs init {self.ref}')}"
                )
            pack_dir, name = pack_child(self.library(), self.ref)
            path = pack_scaffold.scaffold_recipe(pack_dir, name, lock=self.lock)
        if not self.lock:
            self.ctx.diag.warning(NO_LOCK_NOTE.format(path=pack_dir))
        return str(path)


class Validate(RecipeCommand):
    """Validate a pack, recipe, or the whole installed library.

    With no argument it checks every installed pack and `packs.toml`; with a
    ref or path, one pack, recipe or built-in hook. It scans hook modules
    without importing them. A pack that declares hooks needs a `uv.lock` that
    `uv lock --check` accepts. Every `packs.toml` row must carry its
    `content_hash`; a malformed or incomplete row blocks every library change
    until it is fixed. An installed pack whose `pyproject.toml` doesn't parse
    fails. Template and copy sources containing `{{ input }}` tokens are
    checked only up to their literal directory prefix. Any `fail` row exits 1.
    """

    ref: Annotated[Positional[str | None], Document(RecipeFile)] = Field(
        None, description="Installed pack, recipe ref, or explicit path."
    )
    output: Output[Check]

    def run(self) -> Iterator[Check]:
        with recipe_errors():
            library = self.library()
            inspector = PackInspector(library_dir=self.root)
            rows = (
                check_library(library=library, inspector=inspector)
                if self.ref is None
                else [check_ref(self.ref, library=library, inspector=inspector)]
            )
        if self.ref is None and not rows:
            self.empty_hint()
        yield from rows
        if any(row.status == "fail" for row in rows):
            raise Failed("validation failed")
