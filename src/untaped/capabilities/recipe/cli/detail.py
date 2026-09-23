"""Structured detail records for ``recipe show``."""

from __future__ import annotations

from pathlib import Path

from untaped.capabilities.recipe.application.files import read_recipe_file
from untaped.capabilities.recipe.domain.hook_project import hook_module_file
from untaped.capabilities.recipe.domain.pack import HookEntry, PackManifest
from untaped.capabilities.recipe.domain.recipe import (
    CopyStep,
    Recipe,
    RemoveStep,
    TemplateStep,
    TransformStep,
    ValidateStep,
)
from untaped.capabilities.recipe.infrastructure.pack_files import hook_exports


def recipe_detail(ref: str, recipe: Recipe, path: Path) -> dict[str, object]:
    """Return a structured recipe detail record."""
    return {
        "ref": ref,
        "description": recipe.description,
        "inputs": [
            {
                "name": name,
                "type": spec.type,
                "required": spec.required,
                "default": spec.default,
                "description": spec.description,
                "sensitive": spec.sensitive,
            }
            for name, spec in sorted(recipe.inputs.items())
        ],
        "steps": [_step_detail(step) for step in recipe.steps],
        "hooks": sorted(
            {step.hook for step in recipe.steps if isinstance(step, TransformStep | ValidateStep)}
        ),
        "path": str(path),
    }


def hook_detail(
    ref: str,
    entry: HookEntry,
    exports: frozenset[str],
    module_file: Path,
) -> dict[str, object]:
    """Return a structured hook detail record."""
    return {
        "ref": ref,
        "module": entry.module,
        "exports": sorted(exports),
        "path": str(module_file),
    }


def pack_detail(installed_name: str, manifest: PackManifest, root: Path) -> dict[str, object]:
    """Return a structured pack detail record."""
    record: dict[str, object] = {
        "name": installed_name,
        "project": manifest.project_name,
        "version": manifest.version,
        "recipes": [
            {
                "name": name,
                "description": _first_line(read_recipe_file(root / entry.path).description),
            }
            for name, entry in sorted(manifest.recipes.items())
        ],
        "hooks": [
            {
                "name": name,
                "exports": sorted(hook_exports(hook_module_file(root, entry.module))),
            }
            for name, entry in sorted(manifest.hooks.items())
        ],
        "path": str(root),
    }
    if manifest.name != installed_name:
        record["manifest_name"] = manifest.name
    return record


def table_recipe_detail(detail: dict[str, object]) -> dict[str, object]:
    """Flatten a recipe detail's steps into one readable line for the table view."""
    steps = detail.get("steps")
    if not isinstance(steps, list):
        return detail
    return {**detail, "steps": "; ".join(_step_summary(step) for step in steps)}


def _step_detail(
    step: CopyStep | RemoveStep | TemplateStep | TransformStep | ValidateStep,
) -> dict[str, object]:
    files: tuple[Path, ...] = ()
    globs: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    hook = ""
    if isinstance(step, TransformStep | RemoveStep):
        files = (step.file,) if step.file is not None else step.files
        globs, exclude = step.globs, step.exclude
    elif isinstance(step, TemplateStep | CopyStep):
        files = (step.dest,)
    if isinstance(step, TransformStep | ValidateStep):
        hook = step.hook
    return {
        "type": step.type,
        "files": [file.as_posix() for file in files],
        "globs": list(globs),
        "exclude": list(exclude),
        "hook": hook,
    }


def _step_summary(step: object) -> str:
    if not isinstance(step, dict):
        return str(step)
    parts = [str(step.get("type", ""))]
    for key in ("files", "globs", "exclude"):
        values = step.get(key)
        if values:
            joined = ",".join(str(value) for value in values)
            parts.append(joined if key == "files" else f"{key}={joined}")
    if step.get("hook"):
        parts.append(f"hook={step['hook']}")
    return " ".join(parts)


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""
