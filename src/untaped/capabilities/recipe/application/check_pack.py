"""Validate packs, recipes, and the installed library (check use case)."""

from __future__ import annotations

from pathlib import Path

from untaped.capabilities.recipe.application.files import read_recipe_file
from untaped.capabilities.recipe.application.harness import orphaned_test_dirs
from untaped.capabilities.recipe.application.inputs import validate_recipe_input_sources
from untaped.capabilities.recipe.application.ports import PackInspectorPort, PackLibraryPort
from untaped.capabilities.recipe.application.resolution import (
    find_library_recipe,
    is_explicit_recipe_path,
    resolve_explicit_recipe,
)
from untaped.capabilities.recipe.builtins.registry import BUILTIN_HOOKS
from untaped.capabilities.recipe.domain.hook_project import ensure_hook_supports
from untaped.capabilities.recipe.domain.pack import InstalledPack
from untaped.capabilities.recipe.domain.paths import confined_path
from untaped.capabilities.recipe.domain.recipe import (
    CopyStep,
    Recipe,
    TemplateStep,
    TransformStep,
    ValidateStep,
)
from untaped.capabilities.recipe.errors import RecipeNotFoundError
from untaped.capability_api import ConfigError


def check_ref(
    ref_text: str,
    *,
    library: PackLibraryPort,
    inspector: PackInspectorPort,
) -> dict[str, object]:
    """Check one installed pack, recipe ref, or explicit path."""
    if is_explicit_recipe_path(ref_text):
        path = Path(ref_text).expanduser()
        if path.is_dir() and (path / "pyproject.toml").is_file():
            try:
                local = library.local_pack(path)
            except (ValueError, OSError) as exc:
                return _pack_check_row(path.name, path, status="error", error=str(exc))
            return _check_pack(local, inspector)
        resolved = resolve_explicit_recipe(library, path, recipe_id=None)
        return _check_recipe(resolved.path, resolved.ref, resolved.local_hook_project, inspector)
    pack = library.find_pack(ref_text)
    if pack is not None:
        return _check_pack(pack, inspector)
    try:
        pack, name, recipe = find_library_recipe(library, ref_text)
    except RecipeNotFoundError:
        if "/" not in ref_text:
            builtin = BUILTIN_HOOKS.get(ref_text)
            if builtin is not None:
                return _builtin_check_row(ref_text, Path(builtin.module.__file__ or ""))
        raise
    return _check_recipe(pack.root / recipe.path, f"{pack.name}/{name}", pack.root, inspector)


def check_library(
    *,
    library: PackLibraryPort,
    inspector: PackInspectorPort,
) -> list[dict[str, object]]:
    """Check every installed pack plus index/directory reconciliation."""
    rows = [
        _pack_check_row(name, library.packs_dir / name, status="error", error=problem)
        for name, problem in library.reconcile().items()
    ]
    pack_rows = [_check_pack(pack, inspector) for pack in library.packs()]
    pack_rows.extend(
        _pack_check_row(name, library.packs_dir / name, status="error", error=error)
        for name, error in library.load_errors().items()
    )
    rows.extend(sorted(pack_rows, key=lambda row: str(row["pack"])))
    return rows


def _builtin_check_row(name: str, path: Path) -> dict[str, object]:
    return {
        "recipe": name,
        "status": "pass",
        "path": str(path),
        "error": "",
    }


def _pack_check_row(
    name: str,
    path: Path,
    *,
    status: str,
    recipes: int = 0,
    hooks: int = 0,
    error: str = "",
) -> dict[str, object]:
    return {
        "pack": name,
        "status": status,
        "path": str(path),
        "recipes": recipes,
        "hooks": hooks,
        "error": error,
    }


def _check_pack(pack: InstalledPack, inspector: PackInspectorPort) -> dict[str, object]:
    try:
        inspector.check_hook_project(pack.root, pack.manifest)
        for recipe_name, recipe in sorted(pack.manifest.recipes.items()):
            row = _check_recipe(
                pack.root / recipe.path, f"{pack.name}/{recipe_name}", pack.root, inspector
            )
            if row["status"] == "error":
                raise ValueError(f"{recipe_name}: {row['error']}")
        orphans = orphaned_test_dirs(pack)
        if orphans:
            raise ValueError("tests directory names no known recipe: " + ", ".join(orphans))
    except (ConfigError, ValueError, OSError) as exc:
        return _pack_check_row(
            pack.name,
            pack.root,
            status="error",
            recipes=len(pack.manifest.recipes),
            hooks=len(pack.manifest.hooks),
            error=str(exc),
        )
    return _pack_check_row(
        pack.name,
        pack.root,
        status="pass",
        recipes=len(pack.manifest.recipes),
        hooks=len(pack.manifest.hooks),
    )


def _check_recipe(
    recipe_path: Path,
    recipe_ref: str,
    local_hook_project: Path | None,
    inspector: PackInspectorPort,
) -> dict[str, object]:
    try:
        recipe = read_recipe_file(recipe_path)
        validate_recipe_input_sources(recipe)
        _check_assets(recipe, recipe_path.parent)
        _check_local_hook_project(local_hook_project, inspector)
        _check_hooks(recipe, local_hook_project, inspector)
    except (ConfigError, ValueError, OSError) as exc:
        return {
            "recipe": recipe_ref,
            "status": "error",
            "path": str(recipe_path),
            "error": str(exc),
        }
    return {
        "recipe": recipe_ref,
        "status": "pass",
        "path": str(recipe_path),
        "error": "",
    }


def _check_assets(recipe: Recipe, recipe_dir: Path) -> None:
    for step in recipe.steps:
        if isinstance(step, TemplateStep):
            _check_asset(recipe_dir, step.template, field="template", noun="template")
        elif isinstance(step, CopyStep):
            _check_asset(recipe_dir, step.source, field="source", noun="copy source")


def _check_asset(recipe_dir: Path, relative: Path, *, field: str, noun: str) -> None:
    """Check an asset path; ``{{ input }}``-bearing paths check only their literal prefix."""
    parts = relative.parts
    templated = next((index for index, part in enumerate(parts) if "{{" in part), None)
    if templated is None:
        if not confined_path(recipe_dir, relative, field=field).is_file():
            raise ValueError(f"{noun} not found: {relative}")
        return
    if templated == 0:
        return
    prefix = Path(*parts[:templated])
    if not confined_path(recipe_dir, prefix, field=field).is_dir():
        raise ValueError(f"{noun} not found: {relative} (no directory {prefix})")


def _check_local_hook_project(
    local_hook_project: Path | None,
    inspector: PackInspectorPort,
) -> None:
    if local_hook_project is None or not (local_hook_project / "pyproject.toml").is_file():
        return
    manifest = inspector.read_hook_project(local_hook_project)
    if manifest.hooks:
        inspector.check_hook_project(local_hook_project, manifest)


def _check_hooks(
    recipe: Recipe,
    local_hook_project: Path | None,
    inspector: PackInspectorPort,
) -> None:
    for step in recipe.steps:
        if isinstance(step, TransformStep | ValidateStep):
            exports = inspector.hook_exports(step.hook, local_hook_project)
            ensure_hook_supports(exports, step.hook, verb=step.type)
