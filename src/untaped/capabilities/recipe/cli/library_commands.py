"""Library commands: ``add``, ``sync``, ``list``, ``get``, ``validate``, ``remove``, ``edit``."""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from untaped.capabilities.recipe.application.check_pack import check_library, check_ref
from untaped.capabilities.recipe.application.files import read_recipe_file
from untaped.capabilities.recipe.application.resolution import existing_path_hint
from untaped.capabilities.recipe.builtins.registry import BUILTIN_HOOKS
from untaped.capabilities.recipe.cli._context import recipe_ui
from untaped.capabilities.recipe.cli.common import library_root, report_config_errors
from untaped.capabilities.recipe.cli.detail import (
    hook_detail,
    pack_detail,
    recipe_detail,
    table_recipe_detail,
)
from untaped.capabilities.recipe.domain.hook_project import hook_module_file
from untaped.capabilities.recipe.domain.pack import (
    HookEntry,
    InstalledPack,
    PackManifest,
    RecipeEntry,
    parse_ref,
)
from untaped.capabilities.recipe.infrastructure.pack_files import hook_exports, read_pack_manifest
from untaped.capabilities.recipe.infrastructure.pack_inspector import PackInspector
from untaped.capabilities.recipe.infrastructure.pack_store import (
    PackLibrary,
    fetch_pack_source,
    is_git_url,
    local_edits_message,
    pack_content_hash,
    validate_pack,
)
from untaped.capability_api import (
    ColumnsOption,
    ConfigError,
    DryRunOption,
    FormatOption,
    OutcomeRecord,
    UsageError,
    YesOption,
    batch_apply,
    echo,
    emit,
    finish,
    not_found,
    plural,
    q,
    render_rows,
    resolve_each,
    run_editor,
)

_EMPTY_LIBRARY_HINT = (
    "no packs installed; scaffold one with `untaped recipe init pack NAME` "
    "or install one with `untaped recipe add PATH|GIT_URL`"
)


class PackOutcomeRecord(OutcomeRecord):
    """An installed pack's ``add``/``sync``/``remove`` result.

    Kinds ``recipe.add_outcome`` (``action``: ``created``/``updated``),
    ``recipe.sync_outcome`` (``updated``/``unchanged``, or ``planned`` with
    --dry-run) and ``recipe.remove_outcome`` (``removed``, or ``planned``).
    """

    name: str
    source: str | None = None
    rev: str | None = None


@dataclass(frozen=True)
class _SyncPlan:
    """An installed pack and its freshly fetched source tree."""

    pack: InstalledPack
    source_dir: Path
    changed: bool


@dataclass(frozen=True)
class _ResolvedTarget:
    """A pack/recipe/hook/builtin ref resolved for `get` and `edit`."""

    pack: InstalledPack | None = None
    name: str | None = None
    recipe: RecipeEntry | None = None
    hook: HookEntry | None = None
    builtin: str | None = None


def add_command(
    source: Annotated[str, Parameter(help="Pack project path or git URL.")],
    /,
    *,
    rev: Annotated[str | None, Parameter(name="--rev", help="Git revision to install.")] = None,
    name: Annotated[
        str | None,
        Parameter(name="--name", help="Installed pack identity override."),
    ] = None,
    force: Annotated[
        bool,
        Parameter(name="--force", negative="", help="Replace an existing installed pack."),
    ] = False,
    discard_edits: Annotated[
        bool,
        Parameter(
            name="--discard-edits",
            negative="",
            help="With --force, overwrite local edits made to the library copy.",
        ),
    ] = False,
    yes: Annotated[
        bool,
        Parameter(
            name=["--yes", "-y"],
            negative="",
            show=False,
            help="Accepted for compatibility; add never prompts.",
        ),
    ] = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Install a recipe pack from a path or git URL.

    Installing never prompts: only --force (and --discard-edits for a library
    copy with local edits) replaces an installed pack.
    """
    del yes
    with report_config_errors(), tempfile.TemporaryDirectory() as temp_root:
        if rev is not None and not is_git_url(source):
            raise UsageError("--rev is only valid for git URL sources")
        if not is_git_url(source):
            # Record where the pack came from, not where the shell happened to be.
            source = str(Path(source).expanduser().absolute())
        source_dir = (
            fetch_pack_source(source, rev=rev, dest=Path(temp_root) / "pack")
            if is_git_url(source)
            else Path(source)
        )
        manifest = read_pack_manifest(source_dir)
        # Validate before printing the pack summary: error output leads, and
        # the summary follows only on a pack that will actually install.
        validate_pack(source_dir, manifest)
        installed_name = name or manifest.name
        library = PackLibrary(library_root=library_root())
        edited = force and library.local_edits(installed_name)
        if edited and not discard_edits:
            raise ConfigError(local_edits_message(installed_name))
        _render_pack_add_preview(installed_name, manifest, local_edits=edited)
        replaced = library.find_pack(installed_name) is not None
        library.add(
            source_dir,
            source=source,
            rev=rev,
            name=name,
            force=force,
            discard_edits=discard_edits,
        )
        record = PackOutcomeRecord(
            name=installed_name,
            action="updated" if replaced else "created",
            source=source,
            rev=rev,
        )
        emit(record.model_dump(), fmt=fmt, columns=columns, kind="recipe.add_outcome")


def sync_command(
    names: Annotated[
        list[str] | None,
        Parameter(help="Installed pack names (or pass --all).", negative=""),
    ] = None,
    /,
    *,
    all_packs: Annotated[
        bool, Parameter(name="--all", negative="", help="Sync every installed pack.")
    ] = False,
    discard_edits: Annotated[
        bool,
        Parameter(
            name="--discard-edits",
            negative="",
            help="Overwrite local edits made to the library copy.",
        ),
    ] = False,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Re-fetch installed packs from their recorded source and rev.

    Packs whose content would change are listed and confirmed first; the rest
    report ``unchanged``.
    """
    with report_config_errors(), tempfile.TemporaryDirectory() as temp_root:
        library = PackLibrary(library_root=library_root())
        selected = _sync_selection(library, names or [], all_packs=all_packs)
        plans, fetch_failed = resolve_each(
            list(selected),
            _as_config_error(
                lambda name: _fetch_for_sync(
                    library, selected[name], Path(temp_root), discard_edits=discard_edits
                )
            ),
        )
        outcome = batch_apply(
            [plan for plan in plans if plan.changed],
            _as_config_error(
                lambda plan: _install_for_sync(library, plan, discard_edits=discard_edits)
            ),
            verb="sync",
            noun="pack",
            label=lambda plan: plan.pack.name,
            describe=lambda plan: _sync_row(plan, action="planned"),
            ui=recipe_ui(),
            destructive=True,
            assume_yes=yes,
            preview_only=dry_run,
            preview=_sync_preview,
        )
        if outcome.cancelled:
            finish(outcome)
        synced = {plan.pack.name for plan, _ in outcome.results}
        rows = [
            _sync_row(plan, action=action)
            for plan in plans
            if (action := _sync_action(plan, synced=synced, dry_run=dry_run))
        ]
        rendered = render_rows(rows, fmt=fmt, columns=columns, kind="recipe.sync_outcome")
        if rendered:
            echo(rendered)
        finish(fetch_failed or outcome.any_failed)


def _sync_selection(
    library: PackLibrary, names: list[str], *, all_packs: bool
) -> dict[str, InstalledPack]:
    """The installed packs ``sync`` acts on, by name: the named ones, or all with ``--all``."""
    if bool(names) == all_packs:
        raise UsageError("name the packs to sync, or pass --all (not both)")
    installed = {pack.name: pack for pack in library.packs()}
    if all_packs:
        return {name: installed[name] for name in sorted(installed)}
    for name in names:
        if name not in installed:
            raise ConfigError(not_found("pack", name, known=sorted(installed)))
    return {name: installed[name] for name in names}


def _as_config_error[T, R](action: Callable[[T], R]) -> Callable[[T], R]:
    """Wrap ``action`` so its expected library errors are per-item ``ConfigError``s."""

    def wrapped(item: T) -> R:
        try:
            return action(item)
        except (ValueError, OSError) as exc:
            raise ConfigError(str(exc)) from exc

    return wrapped


def _install_for_sync(library: PackLibrary, plan: _SyncPlan, *, discard_edits: bool) -> None:
    library.add(
        plan.source_dir,
        source=plan.pack.source,
        rev=plan.pack.rev or None,
        name=plan.pack.name,
        force=True,
        discard_edits=discard_edits,
    )


def _sync_preview(rows: Sequence[dict[str, object]]) -> None:
    echo(f"About to sync {plural(len(rows), 'pack')}:", err=True)
    for row in rows:
        at = f"@{row['rev']}" if row["rev"] else ""
        echo(f"  - {row['name']} from {row['source']}{at}", err=True)


def _sync_action(plan: _SyncPlan, *, synced: set[str], dry_run: bool) -> str | None:
    """The outcome ``action`` of one fetched pack; ``None`` when its install failed."""
    if not plan.changed:
        return "unchanged"
    if dry_run:
        return "planned"
    return "updated" if plan.pack.name in synced else None


def _fetch_for_sync(
    library: PackLibrary, pack: InstalledPack, temp_root: Path, *, discard_edits: bool
) -> _SyncPlan:
    """Fetch ``pack``'s recorded source and tell whether installing it changes files."""
    if not pack.source:
        raise ValueError("no recorded source; reinstall the pack with `untaped recipe add`")
    if is_git_url(pack.source):
        source_dir = fetch_pack_source(
            pack.source, rev=pack.rev or None, dest=temp_root / pack.name
        )
    else:
        source_dir = Path(pack.source).expanduser()
        if not source_dir.is_dir():
            raise ValueError(f"pack source not found: {pack.source}")
    validate_pack(source_dir, read_pack_manifest(source_dir))
    changed = pack_content_hash(source_dir) != pack_content_hash(pack.root)
    if changed and not discard_edits and library.local_edits(pack.name):
        raise ValueError(local_edits_message(pack.name))
    return _SyncPlan(pack=pack, source_dir=source_dir, changed=changed)


def _sync_row(plan: _SyncPlan, *, action: str) -> dict[str, object]:
    return PackOutcomeRecord(
        name=plan.pack.name,
        action=action,
        source=plan.pack.source,
        rev=plan.pack.rev or None,
    ).model_dump()


def list_command(
    *,
    hooks: Annotated[
        bool,
        Parameter(name="--hooks", negative="", help="List hooks instead of recipes."),
    ] = False,
    packs: Annotated[
        bool,
        Parameter(name="--packs", negative="", help="List installed packs."),
    ] = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List installed recipes, hooks, or packs."""
    with report_config_errors():
        if hooks and packs:
            raise UsageError("--hooks and --packs cannot be combined")
        library = PackLibrary(library_root=library_root())
        installed = library.packs()
        for name, error in library.load_errors().items():
            recipe_ui().message("warning", f"skipping pack '{name}': {error}")
        if packs:
            rows = [_pack_row(pack) for pack in installed]
            kind = "recipe.pack"
        elif hooks:
            rows = [
                _hook_row(pack, name, entry) for pack in installed for name, entry in _hooks(pack)
            ]
            rows.extend(_builtin_hook_row(name) for name in sorted(BUILTIN_HOOKS))
            kind = "recipe.hook"
        else:
            rows = [
                _recipe_row(pack, name, entry)
                for pack in installed
                for name, entry in _recipes(pack)
            ]
            kind = "recipe.recipe"
        rendered = render_rows(rows, fmt=fmt, columns=columns, kind=kind)
        if rendered:
            echo(rendered)
        # The hint is human guidance: structured formats stay machine-clean.
        if fmt == "table" and not installed and not (hooks and BUILTIN_HOOKS):
            recipe_ui().message(
                "info",
                _EMPTY_LIBRARY_HINT,
            )


def get_command(
    ref_text: Annotated[str, Parameter(help="Pack, recipe, or hook ref.")],
    /,
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Show an installed pack, recipe, or hook."""
    with report_config_errors():
        library = PackLibrary(library_root=library_root())
        target = _resolve_target(library, ref_text)
        if target.builtin is not None:
            builtin = BUILTIN_HOOKS[target.builtin]
            emit(
                hook_detail(
                    target.builtin,
                    HookEntry(module=builtin.module.__name__),
                    builtin.exports,
                    Path(builtin.module.__file__ or ""),
                ),
                fmt=fmt,
                columns=columns,
                kind="recipe.hook",
            )
            return
        assert target.pack is not None
        if target.recipe is not None:
            recipe_path = target.pack.root / target.recipe.path
            detail = recipe_detail(
                f"{target.pack.name}/{target.name}",
                read_recipe_file(recipe_path),
                recipe_path,
            )
            emit(
                table_recipe_detail(detail) if fmt == "table" else detail,
                fmt=fmt,
                columns=columns,
                kind="recipe.recipe",
            )
        elif target.hook is not None:
            module_file = hook_module_file(target.pack.root, target.hook.module)
            emit(
                hook_detail(
                    f"{target.pack.name}/{target.name}",
                    target.hook,
                    hook_exports(module_file),
                    module_file,
                ),
                fmt=fmt,
                columns=columns,
                kind="recipe.hook",
            )
        else:
            emit(
                pack_detail(target.pack.name, target.pack.manifest, target.pack.root),
                fmt=fmt,
                columns=columns,
                kind="recipe.pack",
            )


def validate_command(
    ref_text: Annotated[
        str | None,
        Parameter(help="Installed pack, recipe ref, or explicit path."),
    ] = None,
    /,
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Validate a pack, recipe, or the whole installed library."""
    with report_config_errors():
        root = library_root()
        library = PackLibrary(library_root=root)
        inspector = PackInspector(library_root=root)
        rows = (
            check_library(library=library, inspector=inspector)
            if ref_text is None
            else [check_ref(ref_text, library=library, inspector=inspector)]
        )
        rendered = render_rows(rows, fmt=fmt, columns=columns, kind="recipe.check")
        if rendered:
            echo(rendered)
        if ref_text is None and not rows:
            recipe_ui().message(
                "info",
                _EMPTY_LIBRARY_HINT,
            )
        finish(any(row["status"] == "error" for row in rows))


def remove_command(
    name: Annotated[str, Parameter(help="Installed pack identity.")],
    /,
    *,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Remove an installed pack."""
    with report_config_errors():
        library = PackLibrary(library_root=library_root())

        def _remove(item: str) -> str:
            library.remove(item)
            return item

        def _preview(rows: Sequence[dict[str, object]]) -> None:
            echo(f"About to remove {plural(len(rows), 'pack')}:", err=True)
            for row in rows:
                echo(f"  - {row['name']}", err=True)
            if library.local_edits(name):
                recipe_ui().message(
                    "warning",
                    f"pack {q(name)} has local edits in the library "
                    "(via edit or init recipe/hook); removing discards them",
                )

        outcome = batch_apply(
            [name],
            _remove,
            verb="remove",
            noun="pack",
            label=str,
            describe=lambda item: {"name": item},
            ui=recipe_ui(),
            destructive=True,
            assume_yes=yes,
            preview_only=dry_run,
            preview=_preview,
        )
        if dry_run:
            rows = [PackOutcomeRecord(name=name, action="planned").model_dump()]
        else:
            rows = [
                PackOutcomeRecord(name=item, action="removed").model_dump()
                for item, _ in outcome.results
            ]
        rendered = render_rows(rows, fmt=fmt, columns=columns, kind="recipe.remove_outcome")
        if rendered:
            echo(rendered)
        finish(outcome)


def edit_command(ref_text: Annotated[str, Parameter(help="Pack, recipe, or hook ref.")], /) -> None:
    """Open a pack pyproject, recipe file, or hook module in $VISUAL or $EDITOR."""
    with report_config_errors():
        library = PackLibrary(library_root=library_root())
        target = _resolve_target(library, ref_text)
        if target.builtin is not None:
            raise ConfigError(
                f"built-in hooks are engine-owned and cannot be edited: {target.builtin}"
            )
        assert target.pack is not None
        if target.recipe is not None:
            run_editor(target.pack.root / target.recipe.path)
        elif target.hook is not None:
            run_editor(hook_module_file(target.pack.root, target.hook.module))
        else:
            run_editor(target.pack.root / "pyproject.toml")


def _resolve_target(library: PackLibrary, ref_text: str) -> _ResolvedTarget:
    """Resolve a ref to a pack, recipe, or hook, preferring recipes' not-found error."""
    pack = library.find_pack(ref_text)
    if pack is not None:
        return _ResolvedTarget(pack=pack)
    ref = parse_ref(ref_text)
    try:
        recipe_pack, recipe = library.find_recipe(ref)
    except ValueError as recipe_error:
        try:
            hook_pack, hook = library.find_hook(ref)
        except ValueError:
            if "/" not in ref_text and ref_text in BUILTIN_HOOKS:
                return _ResolvedTarget(name=ref_text, builtin=ref_text)
            if str(recipe_error).startswith("recipe not found"):
                raise ValueError(f"{recipe_error}{existing_path_hint(ref_text)}") from None
            raise recipe_error from None
        return _ResolvedTarget(pack=hook_pack, name=ref.name, hook=hook)
    return _ResolvedTarget(pack=recipe_pack, name=ref.name, recipe=recipe)


def _recipes(pack: InstalledPack) -> list[tuple[str, RecipeEntry]]:
    return sorted(pack.manifest.recipes.items())


def _hooks(pack: InstalledPack) -> list[tuple[str, HookEntry]]:
    return sorted(pack.manifest.hooks.items())


def _pack_row(pack: InstalledPack) -> dict[str, object]:
    return {
        "name": pack.name,
        "version": pack.installed_version,
        "path": str(pack.root),
        "source": pack.source,
        "rev": pack.rev,
        "recipes": len(pack.manifest.recipes),
        "hooks": len(pack.manifest.hooks),
    }


def _recipe_row(pack: InstalledPack, name: str, entry: RecipeEntry) -> dict[str, object]:
    return {
        "pack": pack.name,
        "name": name,
        "ref": f"{pack.name}/{name}",
        "path": str(pack.root / entry.path),
    }


def _hook_row(pack: InstalledPack, name: str, entry: HookEntry) -> dict[str, object]:
    return {
        "pack": pack.name,
        "name": name,
        "ref": f"{pack.name}/{name}",
        "module": entry.module,
        "path": str(hook_module_file(pack.root, entry.module)),
    }


def _builtin_hook_row(name: str) -> dict[str, object]:
    module = BUILTIN_HOOKS[name].module
    return {
        "pack": "(builtin)",
        "name": name,
        "ref": name,
        "module": module.__name__,
        "path": module.__file__ or "",
    }


def _render_pack_add_preview(
    installed_name: str,
    manifest: PackManifest,
    *,
    local_edits: bool = False,
) -> None:
    ui = recipe_ui()
    recipes = ", ".join(sorted(manifest.recipes)) or "(none)"
    hooks = ", ".join(sorted(manifest.hooks)) or "(none)"
    ui.message("info", f"Pack: {installed_name}")
    ui.message("info", f"Recipes: {recipes}")
    ui.message("info", f"Hooks: {hooks}")
    if local_edits:
        ui.message("warning", "library copy has local edits; --discard-edits will overwrite them")
