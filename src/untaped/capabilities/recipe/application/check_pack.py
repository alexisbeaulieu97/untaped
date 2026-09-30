"""Validate packs, recipes, and the installed library (check use case)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

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
from untaped.capability_api import CheckRecord, UntapedError

CheckedType = Literal["pack", "recipe", "hook"]
"""What a ``recipe validate`` row checked."""


class LibraryCheckRecord(CheckRecord):
    """One ``recipe validate`` row (kind ``recipe.check``).

    ``name`` is the pack name, the ``PACK/RECIPE`` ref, or the built-in hook
    name, and ``type`` says which, so a listing mixing packs and recipes keeps
    one identifying column. ``status`` is ``pass`` or ``fail``; ``detail``
    says why a check failed.
    """

    name: str
    type: CheckedType
    status: Literal["pass", "fail"]
    path: str
    detail: str | None = None


def check_ref(
    ref_text: str,
    *,
    library: PackLibraryPort,
    inspector: PackInspectorPort,
) -> LibraryCheckRecord:
    """Check one installed pack, recipe ref, or explicit path."""
    if is_explicit_recipe_path(ref_text):
        path = Path(ref_text).expanduser()
        if path.is_dir() and (path / "pyproject.toml").is_file():
            try:
                local = library.local_pack(path)
            except (ValueError, OSError) as exc:
                return _failed_pack(path.name, path, str(exc))
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
                return LibraryCheckRecord(
                    name=ref_text,
                    type="hook",
                    status="pass",
                    path=builtin.module.__file__ or "",
                )
        raise
    return _check_recipe(pack.root / recipe.path, f"{pack.name}/{name}", pack.root, inspector)


def check_library(
    *,
    library: PackLibraryPort,
    inspector: PackInspectorPort,
) -> list[LibraryCheckRecord]:
    """Check every installed pack plus index/directory reconciliation."""
    rows = [
        _failed_pack(name, library.packs_dir / name, problem)
        for name, problem in library.reconcile().items()
    ]
    pack_rows = [_check_pack(pack, inspector) for pack in library.packs()]
    pack_rows.extend(
        _failed_pack(name, library.packs_dir / name, error)
        for name, error in library.load_errors().items()
    )
    rows.extend(sorted(pack_rows, key=lambda row: row.name))
    return rows


def _failed_pack(name: str, path: Path, detail: str) -> LibraryCheckRecord:
    return LibraryCheckRecord(name=name, type="pack", status="fail", path=str(path), detail=detail)


def _check_pack(pack: InstalledPack, inspector: PackInspectorPort) -> LibraryCheckRecord:
    try:
        inspector.check_hook_project(pack.root, pack.manifest)
        for recipe_name, recipe in sorted(pack.manifest.recipes.items()):
            row = _check_recipe(
                pack.root / recipe.path, f"{pack.name}/{recipe_name}", pack.root, inspector
            )
            if row.status == "fail":
                raise ValueError(f"{recipe_name}: {row.detail}")
        orphans = orphaned_test_dirs(pack)
        if orphans:
            raise ValueError("tests directory names no known recipe: " + ", ".join(orphans))
    except (UntapedError, ValueError, OSError) as exc:
        return _failed_pack(pack.name, pack.root, str(exc))
    return LibraryCheckRecord(name=pack.name, type="pack", status="pass", path=str(pack.root))


def _check_recipe(
    recipe_path: Path,
    recipe_ref: str,
    local_hook_project: Path | None,
    inspector: PackInspectorPort,
) -> LibraryCheckRecord:
    try:
        recipe = read_recipe_file(recipe_path)
        validate_recipe_input_sources(recipe)
        _check_assets(recipe, recipe_path.parent)
        _check_local_hook_project(local_hook_project, inspector)
        _check_hooks(recipe, local_hook_project, inspector)
    except (UntapedError, ValueError, OSError) as exc:
        return LibraryCheckRecord(
            name=recipe_ref, type="recipe", status="fail", path=str(recipe_path), detail=str(exc)
        )
    return LibraryCheckRecord(name=recipe_ref, type="recipe", status="pass", path=str(recipe_path))


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
