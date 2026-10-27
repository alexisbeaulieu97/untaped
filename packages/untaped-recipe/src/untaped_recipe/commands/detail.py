"""The records of recipes, packs and hooks, for ``list`` and ``get`` alike.

One builder per record, so a ``list`` row and a ``get`` detail of the same
kind cannot drift apart: ``detail`` adds what only ``get`` reads.
"""

from __future__ import annotations

from pathlib import Path

from untaped_recipe.application.files import read_recipe_file
from untaped_recipe.builtins.registry import BUILTIN_HOOKS
from untaped_recipe.domain.hook_project import hook_module_file
from untaped_recipe.domain.pack import InstalledPack
from untaped_recipe.domain.recipe import (
    CopyStep,
    RecipeFile,
    RemoveStep,
    TemplateStep,
    TransformStep,
    ValidateStep,
)
from untaped_recipe.infrastructure.pack_files import hook_exports
from untaped_recipe.records import (
    Hook,
    Pack,
    PackHook,
    PackRecipe,
    Recipe,
    RecipeInput,
    RecipeStep,
)

BUILTIN_PACK = "(builtin)"
"""The ``pack`` of a built-in hook's row."""


def pack_record(pack: InstalledPack, *, detail: bool) -> Pack:
    """An installed pack's row: counts for ``packs list``, summaries for ``packs get``.

    ``detail`` reads each recipe file and scans each hook module; its
    ``version`` is the library copy's manifest version, else the installed one.
    """
    manifest, root = pack.manifest, pack.root
    return Pack(
        name=pack.name,
        project=manifest.project_name,
        version=manifest.version if detail else pack.installed_version,
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
        ]
        if detail
        else len(manifest.recipes),
        hooks=[
            PackHook(
                name=name,
                exports=sorted(hook_exports(hook_module_file(root, entry.module))),
            )
            for name, entry in sorted(manifest.hooks.items())
        ]
        if detail
        else len(manifest.hooks),
        manifest_name=manifest.name if manifest.name != pack.name else None,
    )


def recipe_record(pack: InstalledPack, name: str, *, detail: bool) -> Recipe:
    """One of ``pack``'s recipes; ``detail`` (``recipe get``) reads its file."""
    path = pack.root / pack.manifest.recipes[name].path
    recipe = read_recipe_file(path) if detail else None
    return Recipe(
        pack=pack.name,
        name=name,
        ref=_ref(pack.name, name),
        description=None if recipe is None else recipe.description,
        inputs=None if recipe is None else _inputs(recipe),
        steps=None if recipe is None else [_step_detail(step) for step in recipe.steps],
        hooks=None if recipe is None else _hooks(recipe),
        path=str(path),
    )


def hook_record(pack: InstalledPack | None, name: str, *, detail: bool) -> Hook:
    """One of ``pack``'s hooks, or a built-in (``pack`` ``None``); ``detail`` adds exports."""
    exports: frozenset[str] | None
    if pack is None:
        builtin = BUILTIN_HOOKS[name]
        module, path = builtin.module.__name__, builtin.module.__file__ or ""
        exports = builtin.exports if detail else None
    else:
        module = pack.manifest.hooks[name].module
        module_file = hook_module_file(pack.root, module)
        path = str(module_file)
        exports = hook_exports(module_file) if detail else None
    return Hook(
        pack=BUILTIN_PACK if pack is None else pack.name,
        name=name,
        ref=_ref(None if pack is None else pack.name, name),
        module=module,
        exports=None if exports is None else sorted(exports),
        path=path,
    )


def _ref(pack: str | None, name: str) -> str:
    """A recipe's or hook's ``PACK/NAME`` ref; a built-in hook's is its name."""
    return name if pack is None else f"{pack}/{name}"


def _inputs(recipe: RecipeFile) -> list[RecipeInput]:
    return [
        RecipeInput(
            name=name,
            type=spec.type,
            required=spec.required,
            default=spec.default,
            description=spec.description,
            sensitive=spec.sensitive,
        )
        for name, spec in sorted(recipe.inputs.items())
    ]


def _hooks(recipe: RecipeFile) -> list[str]:
    return sorted(
        {step.hook for step in recipe.steps if isinstance(step, TransformStep | ValidateStep)}
    )


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


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""
