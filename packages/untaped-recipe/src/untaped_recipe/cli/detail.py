"""The detail records of ``recipe get``, ``packs get`` and ``hooks get``."""

from __future__ import annotations

from pathlib import Path

from untaped_recipe.application.files import read_recipe_file
from untaped_recipe.domain.hook_project import hook_module_file
from untaped_recipe.domain.pack import HookEntry, InstalledPack
from untaped_recipe.domain.recipe import (
    CopyStep,
    Recipe,
    RemoveStep,
    TemplateStep,
    TransformStep,
    ValidateStep,
)
from untaped_recipe.infrastructure.pack_files import hook_exports
from untaped_recipe.records import (
    Hook,
    InstalledRecipe,
    Pack,
    PackHook,
    PackRecipe,
    RecipeInput,
    RecipeStep,
)

BUILTIN_PACK = "(builtin)"
"""The ``pack`` of a built-in hook's row."""


def recipe_detail(pack: str, name: str, recipe: Recipe, path: Path) -> InstalledRecipe:
    """The ``recipe get`` record: the recipe's inputs, steps and hooks."""
    return InstalledRecipe(
        pack=pack,
        name=name,
        ref=f"{pack}/{name}",
        description=recipe.description,
        inputs=[
            RecipeInput(
                name=input_name,
                type=spec.type,
                required=spec.required,
                default=spec.default,
                description=spec.description,
                sensitive=spec.sensitive,
            )
            for input_name, spec in sorted(recipe.inputs.items())
        ],
        steps=[_step_detail(step) for step in recipe.steps],
        hooks=sorted(
            {step.hook for step in recipe.steps if isinstance(step, TransformStep | ValidateStep)}
        ),
        path=str(path),
    )


def hook_detail(
    pack: str | None,
    name: str,
    entry: HookEntry,
    exports: frozenset[str],
    module_file: Path,
) -> Hook:
    """The ``hooks get`` record of a pack's hook, or a built-in's (``pack`` ``None``)."""
    return Hook(
        pack=pack or BUILTIN_PACK,
        name=name,
        ref=name if pack is None else f"{pack}/{name}",
        module=entry.module,
        exports=sorted(exports),
        path=str(module_file),
    )


def pack_detail(pack: InstalledPack) -> Pack:
    """The ``packs get`` record: each recipe's summary line and each hook's exports."""
    manifest, root = pack.manifest, pack.root
    return Pack(
        name=pack.name,
        project=manifest.project_name,
        version=manifest.version,
        path=str(root),
        source=pack.source,
        rev=pack.rev,
        commit=pack.commit,
        recipes=[
            PackRecipe(
                name=name,
                description=_first_line(read_recipe_file(root / entry.path).description),
            )
            for name, entry in sorted(manifest.recipes.items())
        ],
        hooks=[
            PackHook(
                name=name,
                exports=sorted(hook_exports(hook_module_file(root, entry.module))),
            )
            for name, entry in sorted(manifest.hooks.items())
        ],
        manifest_name=manifest.name if manifest.name != pack.name else None,
    )


def table_recipe_detail(detail: InstalledRecipe) -> dict[str, object]:
    """A recipe record's table view: its steps flattened into one readable line."""
    return detail.model_dump(mode="json") | {
        "steps": "; ".join(_step_summary(step) for step in detail.steps or ())
    }


def _step_detail(
    step: CopyStep | RemoveStep | TemplateStep | TransformStep | ValidateStep,
) -> RecipeStep:
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
    return RecipeStep(
        type=step.type,
        files=[file.as_posix() for file in files],
        globs=list(globs),
        exclude=list(exclude),
        hook=hook,
    )


def _step_summary(step: RecipeStep) -> str:
    parts = [step.type]
    for key, values in (("files", step.files), ("globs", step.globs), ("exclude", step.exclude)):
        if values:
            joined = ",".join(values)
            parts.append(joined if key == "files" else f"{key}={joined}")
    if step.hook:
        parts.append(f"hook={step.hook}")
    return " ".join(parts)


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""
