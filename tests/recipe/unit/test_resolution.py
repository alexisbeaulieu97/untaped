"""Tests for recipe ref resolution: library refs, explicit paths, and typed misses."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.capabilities.recipe.application.resolution import (
    find_library_recipe,
    resolve_apply_recipe,
    resolve_explicit_recipe,
)
from untaped.capabilities.recipe.errors import RecipeFileNotFoundError, RecipeNotFoundError
from untaped.capabilities.recipe.infrastructure.pack_store import PackLibrary


def _write_pack(root: Path, *, recipes: tuple[str, ...]) -> None:
    rows = []
    for name in recipes:
        recipe = root / "recipes" / f"{name}.yml"
        recipe.parent.mkdir(parents=True, exist_ok=True)
        recipe.write_text("version: 1\nsteps: []\n", encoding="utf-8")
        rows.append(f'"{name}" = {{ path = "recipes/{name}.yml" }}')
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "untaped-recipe-acme"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe]\n"
        'requires_hook_api = ">=0.8,<1"\n\n'
        "[tool.untaped_recipe.recipes]\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")


def _library(tmp_path: Path, *recipes: str) -> PackLibrary:
    source = tmp_path / "source"
    _write_pack(source, recipes=recipes)
    library = PackLibrary(library_root=tmp_path / "library")
    library.add(source, source=str(source), rev=None, name=None, force=False)
    return library


@pytest.mark.parametrize("ref", ["lint", "acme/lint"])
def test_find_library_recipe_resolves_bare_and_qualified_refs(tmp_path: Path, ref: str) -> None:
    library = _library(tmp_path, "lint")

    pack, name, entry = find_library_recipe(library, ref)

    assert (pack.name, name, entry.path) == ("acme", "lint", "recipes/lint.yml")


def test_find_library_recipe_miss_is_typed_and_hints_an_existing_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = _library(tmp_path, "lint")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "demo").mkdir()

    with pytest.raises(RecipeNotFoundError) as caught:
        find_library_recipe(library, "demo")

    assert str(caught.value).startswith("recipe not found: 'demo' (a path named 'demo' exists")


def test_apply_ref_falls_back_to_a_single_recipe_pack(tmp_path: Path) -> None:
    library = _library(tmp_path, "lint")

    resolved = resolve_apply_recipe(library, "acme", recipe_id=None)

    assert resolved.ref == "acme/lint"
    assert resolved.path == library.packs_dir / "acme" / "recipes" / "lint.yml"


def test_explicit_path_misses_are_typed(tmp_path: Path) -> None:
    library = _library(tmp_path, "lint")

    with pytest.raises(RecipeFileNotFoundError, match="recipe file not found"):
        resolve_explicit_recipe(library, tmp_path / "nope.yml", recipe_id=None)
    with pytest.raises(RecipeFileNotFoundError, match="recipe file not found"):
        resolve_explicit_recipe(library, tmp_path, recipe_id=None)
    with pytest.raises(RecipeNotFoundError, match="recipe not found: 'nope'"):
        resolve_explicit_recipe(library, tmp_path / "source", recipe_id="nope")
