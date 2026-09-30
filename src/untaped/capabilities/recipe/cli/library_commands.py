"""Library commands: recipe ``list``/``get``/``edit``/``validate``, ``packs …`` and hook reads."""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from untaped.capabilities.recipe.application.check_pack import check_library, check_ref
from untaped.capabilities.recipe.application.files import read_recipe_file
from untaped.capabilities.recipe.application.resolution import find_library_recipe
from untaped.capabilities.recipe.builtins.registry import BUILTIN_HOOKS
from untaped.capabilities.recipe.cli._context import recipe_ui
from untaped.capabilities.recipe.cli.common import (
    as_recipe_error,
    library_root,
    report_config_errors,
)
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
from untaped.capabilities.recipe.errors import (
    AmbiguousRefError,
    HookNotFoundError,
    LocalChangesError,
    PackNotFoundError,
    PartialRemovalError,
    PathNotFoundError,
    RecipeError,
    RecipeNotFoundError,
)
from untaped.capabilities.recipe.infrastructure.pack_files import hook_exports, read_pack_manifest
from untaped.capabilities.recipe.infrastructure.pack_inspector import PackInspector
from untaped.capabilities.recipe.infrastructure.pack_store import (
    PackLibrary,
    changed_hook_files,
    checkout_commit,
    fetch_pack_source,
    is_git_url,
    local_edits_message,
    pack_content_hash,
    validate_pack,
)
from untaped.capability_api import (
    ColumnsOption,
    DryRunOption,
    ErrorInfo,
    FormatOption,
    OutcomeRecord,
    OutputFormat,
    StdinOption,
    UntapedError,
    UsageError,
    YesOption,
    batch_apply,
    echo,
    emit,
    finish,
    hint,
    not_found,
    plural,
    q,
    read_identifiers,
    render_rows,
    report_error,
    run_editor,
)

_EMPTY_LIBRARY_HINT = (
    "no packs installed; scaffold one with `untaped recipe packs init NAME` "
    "or install one with `untaped recipe packs add PATH|GIT_URL`"
)


class PackOutcomeRecord(OutcomeRecord):
    """An installed pack's ``add``/``sync``/``remove`` result.

    Kinds ``recipe.add_outcome`` (``action``: ``created``/``updated``),
    ``recipe.sync_outcome`` (``updated``/``unchanged``, or ``planned`` with
    --dry-run) and ``recipe.remove_outcome`` (``removed``, or ``planned``).
    A pack that could not be fetched, installed or removed is ``failed``,
    with ``detail`` and ``error``. ``commit`` is the resolved commit of a git
    source (``rev`` is the one asked for).
    """

    name: str
    source: str | None = None
    rev: str | None = None
    commit: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class _SyncPlan:
    """An installed pack and its freshly fetched source tree (at ``commit`` for git)."""

    pack: InstalledPack
    source_dir: Path
    changed: bool
    commit: str | None = None
    hook_changes: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ResolvedHook:
    """A hook ref resolved for ``hooks get``/``hooks edit``: a pack hook or a built-in."""

    name: str
    pack: InstalledPack | None = None
    hook: HookEntry | None = None


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
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Install a recipe pack from a path or git URL.

    Installing never prompts: only --force (and --discard-edits for a library
    copy with local edits) replaces an installed pack.
    """
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
        commit = checkout_commit(source_dir) if is_git_url(source) else None
        manifest = read_pack_manifest(source_dir)
        # Validate before printing the pack summary: error output leads, and
        # the summary follows only on a pack that will actually install.
        validate_pack(source_dir, manifest)
        installed_name = name or manifest.name
        library = PackLibrary(library_root=library_root())
        edited = force and library.local_edits(installed_name)
        if edited and not discard_edits:
            raise LocalChangesError(local_edits_message(installed_name))
        _render_pack_add_preview(installed_name, manifest, local_edits=edited)
        replaced = library.find_pack(installed_name) is not None
        library.add(
            source_dir,
            source=source,
            rev=rev,
            commit=commit,
            name=name,
            force=force,
            discard_edits=discard_edits,
        )
        record = PackOutcomeRecord(
            name=installed_name,
            action="updated" if replaced else "created",
            source=source,
            rev=rev,
            commit=commit,
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
    stdin: Annotated[
        StdinOption, Parameter(help="Read pack names, or recipe.pack pipe records, from stdin.")
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

    Packs whose content would change are listed and confirmed first, with the
    commit move and the hook-code files that change; the rest report
    ``unchanged``.
    """
    with report_config_errors(), tempfile.TemporaryDirectory() as temp_root:
        library = PackLibrary(library_root=library_root())
        selected = _sync_selection(
            library, _pack_names(names, stdin=stdin) if stdin else names or [], all_packs=all_packs
        )

        @as_recipe_error
        def fetch(pack: InstalledPack) -> _SyncPlan:
            return _fetch_for_sync(library, pack, Path(temp_root), discard_edits=discard_edits)

        plans: list[_SyncPlan] = []
        failed: dict[str, UntapedError] = {}
        for name, pack in selected.items():
            try:
                plans.append(fetch(pack))
            except UntapedError as exc:
                report_error(exc, item=name)
                failed[name] = exc
        changed = [plan for plan in plans if plan.changed]
        if dry_run and changed:
            _sync_preview(changed)
        outcome = batch_apply(
            changed,
            as_recipe_error(
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
            preview=lambda _rows: _sync_preview(changed),
        )
        if outcome.cancelled:
            finish(outcome)
        if not dry_run:
            # An unchanged pack still moved to the fetched commit (older
            # installs recorded none; a commit may touch only ignored files).
            for plan in plans:
                if not plan.changed and plan.commit and plan.commit != plan.pack.commit:
                    library.record_commit(plan.pack.name, plan.commit)
        failed.update((plan.pack.name, exc) for plan, exc in outcome.failures)
        by_name = {plan.pack.name: plan for plan in plans}
        rows = [
            _pack_outcome(name, pack, action="failed", error=failed[name])
            if name in failed
            else _sync_row(by_name[name], action=_sync_action(by_name[name], dry_run=dry_run))
            for name, pack in selected.items()
        ]
        rendered = render_rows(rows, fmt=fmt, columns=columns, kind="recipe.sync_outcome")
        if rendered:
            echo(rendered)
        finish(bool(failed))


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
            raise PackNotFoundError(not_found("pack", name, known=sorted(installed)))
    return {name: installed[name] for name in names}


def _install_for_sync(library: PackLibrary, plan: _SyncPlan, *, discard_edits: bool) -> None:
    library.add(
        plan.source_dir,
        source=plan.pack.source,
        rev=plan.pack.rev or None,
        commit=plan.commit,
        name=plan.pack.name,
        force=True,
        discard_edits=discard_edits,
    )


def _sync_preview(plans: Sequence[_SyncPlan]) -> None:
    """List the packs about to sync: source, commit move, and changed hook code."""
    echo(f"About to sync {plural(len(plans), 'pack')}:", err=True)
    for plan in plans:
        pack = plan.pack
        at = f"@{pack.rev}" if pack.rev else ""
        move = (
            f" ({_short_commit(pack.commit)} -> {_short_commit(plan.commit)})"
            if plan.commit
            else ""
        )
        echo(f"  - {pack.name} from {pack.source}{at}{move}", err=True)
        if plan.hook_changes:
            echo(f"    hook code changed: {', '.join(plan.hook_changes)}", err=True)
        else:
            echo("    hook code unchanged", err=True)


def _short_commit(commit: str | None) -> str:
    return commit[:12] if commit else "unrecorded"


def _sync_action(plan: _SyncPlan, *, dry_run: bool) -> str:
    """The outcome ``action`` of one fetched pack whose install did not fail."""
    if not plan.changed:
        return "unchanged"
    return "planned" if dry_run else "updated"


def _fetch_for_sync(
    library: PackLibrary, pack: InstalledPack, temp_root: Path, *, discard_edits: bool
) -> _SyncPlan:
    """Fetch ``pack``'s recorded source and tell whether installing it changes files."""
    if not pack.source:
        raise ValueError("no recorded source; reinstall the pack with `untaped recipe packs add`")
    commit = None
    if is_git_url(pack.source):
        source_dir = fetch_pack_source(
            pack.source, rev=pack.rev or None, dest=temp_root / pack.name
        )
        commit = checkout_commit(source_dir)
    else:
        source_dir = Path(pack.source).expanduser()
        if not source_dir.is_absolute():
            # Older installs recorded the path as typed; resolving it against
            # today's working directory could install a different pack.
            raise ValueError(
                f"recorded source {q(pack.source)} is a relative path; "
                "reinstall the pack with `untaped recipe packs add --force`"
            )
        if not source_dir.is_dir():
            raise PathNotFoundError(f"pack source not found: {pack.source}")
    validate_pack(source_dir, read_pack_manifest(source_dir))
    changed = pack_content_hash(source_dir) != pack_content_hash(pack.root)
    if changed and not discard_edits and library.local_edits(pack.name):
        raise LocalChangesError(local_edits_message(pack.name))
    hook_changes = tuple(changed_hook_files(pack.root, source_dir)) if changed else ()
    return _SyncPlan(
        pack=pack,
        source_dir=source_dir,
        changed=changed,
        commit=commit,
        hook_changes=hook_changes,
    )


def _sync_row(plan: _SyncPlan, *, action: str) -> dict[str, object]:
    return PackOutcomeRecord(
        name=plan.pack.name,
        action=action,
        source=plan.pack.source,
        rev=plan.pack.rev or None,
        commit=plan.commit,
    ).model_dump()


def _pack_outcome(
    name: str,
    pack: InstalledPack | None,
    *,
    action: str,
    error: UntapedError | None = None,
) -> dict[str, object]:
    """A row with ``pack``'s recorded source, rev and commit (none for an unloadable pack).

    A failed row's ``error`` was already reported (and counted) on stderr.
    """
    info = None if error is None else ErrorInfo.from_exception(error)
    return PackOutcomeRecord(
        name=name,
        action=action,
        source=(pack.source or None) if pack else None,
        rev=(pack.rev or None) if pack else None,
        commit=(pack.commit or None) if pack else None,
        detail=None if info is None else info.message,
        error=info,
    ).model_dump()


def list_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """List installed recipes."""
    _list_rows(
        lambda installed: [
            _recipe_row(pack, name, entry) for pack in installed for name, entry in _recipes(pack)
        ],
        kind="recipe.recipe",
        fmt=fmt,
        columns=columns,
        table_columns=["pack", "name"],
    )


def list_packs_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """List installed packs."""
    _list_rows(
        lambda installed: [_pack_row(pack) for pack in installed],
        kind="recipe.pack",
        fmt=fmt,
        columns=columns,
        table_columns=["name", "version", "source", "rev", "recipes", "hooks"],
    )


def list_hooks_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """List installed hooks, then the built-in ones."""
    _list_rows(
        lambda installed: [
            *(_hook_row(pack, name, entry) for pack in installed for name, entry in _hooks(pack)),
            *(_builtin_hook_row(name) for name in sorted(BUILTIN_HOOKS)),
        ],
        kind="recipe.hook",
        fmt=fmt,
        columns=columns,
        table_columns=["pack", "name", "module"],
        hint_when_empty=not BUILTIN_HOOKS,
    )


def _list_rows(
    rows_of: Callable[[list[InstalledPack]], list[dict[str, object]]],
    *,
    kind: str,
    fmt: OutputFormat,
    columns: list[str] | None,
    table_columns: list[str],
    hint_when_empty: bool = True,
) -> None:
    """Render one row kind for every loadable installed pack, warning about the rest."""
    with report_config_errors():
        library = PackLibrary(library_root=library_root())
        installed = library.packs()
        for name, error in library.load_errors().items():
            recipe_ui().message("warning", f"skipping pack '{name}': {error}")
        rendered = render_rows(
            rows_of(installed), fmt=fmt, columns=columns, kind=kind, table_columns=table_columns
        )
        if rendered:
            echo(rendered)
        # The hint is human guidance: structured formats stay machine-clean.
        if fmt == "table" and not installed and hint_when_empty:
            recipe_ui().message("info", _EMPTY_LIBRARY_HINT)


def get_command(
    ref_text: Annotated[str, Parameter(help="Recipe name or PACK/RECIPE reference.")],
    /,
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Show an installed recipe."""
    with report_config_errors():
        library = PackLibrary(library_root=library_root())
        pack, name, recipe = _find_recipe(library, ref_text, verb="get")
        recipe_path = pack.root / recipe.path
        detail = recipe_detail(f"{pack.name}/{name}", read_recipe_file(recipe_path), recipe_path)
        emit(
            table_recipe_detail(detail) if fmt == "table" else detail,
            fmt=fmt,
            columns=columns,
            kind="recipe.recipe",
        )


def get_pack_command(
    name: Annotated[str, Parameter(help="Installed pack identity.")],
    /,
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Show an installed pack."""
    with report_config_errors():
        pack = _find_pack(PackLibrary(library_root=library_root()), name)
        emit(
            pack_detail(pack.name, pack.manifest, pack.root),
            fmt=fmt,
            columns=columns,
            kind="recipe.pack",
        )


def get_hook_command(
    ref_text: Annotated[str, Parameter(help="Hook name, PACK/HOOK reference, or built-in.")],
    /,
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Show an installed or built-in hook."""
    with report_config_errors():
        target = _find_hook(PackLibrary(library_root=library_root()), ref_text)
        if target.pack is None or target.hook is None:
            builtin = BUILTIN_HOOKS[target.name]
            detail = hook_detail(
                target.name,
                HookEntry(module=builtin.module.__name__),
                builtin.exports,
                Path(builtin.module.__file__ or ""),
            )
        else:
            module_file = hook_module_file(target.pack.root, target.hook.module)
            detail = hook_detail(
                f"{target.pack.name}/{target.name}",
                target.hook,
                hook_exports(module_file),
                module_file,
            )
        emit(detail, fmt=fmt, columns=columns, kind="recipe.hook")


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
        rendered = render_rows(
            [row.model_dump() for row in rows], fmt=fmt, columns=columns, kind="recipe.check"
        )
        if rendered:
            echo(rendered)
        if ref_text is None and not rows:
            recipe_ui().message(
                "info",
                _EMPTY_LIBRARY_HINT,
            )
        finish(any(row.status == "fail" for row in rows))


def remove_command(
    names: Annotated[
        list[str] | None, Parameter(help="Installed pack identities.", negative="")
    ] = None,
    /,
    *,
    stdin: Annotated[
        StdinOption, Parameter(help="Read pack names, or recipe.pack pipe records, from stdin.")
    ] = False,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Remove installed packs."""
    with report_config_errors():
        library = PackLibrary(library_root=library_root())
        selected = _pack_names(names, stdin=stdin)
        installed = {pack.name: pack for pack in library.packs()}
        # A removal that stopped partway leaves a name only reconcile() still sees.
        known = set(installed) | set(library.load_errors()) | set(library.reconcile())
        for name in selected:
            if name not in known:
                raise PackNotFoundError(not_found("pack", name, known=sorted(known)))

        @as_recipe_error
        def _remove(item: str) -> str:
            library.remove(item)
            return item

        def _preview(rows: Sequence[dict[str, object]]) -> None:
            echo(f"About to remove {plural(len(rows), 'pack')}:", err=True)
            for row in rows:
                echo(f"  - {row['name']}", err=True)
            for name in selected:
                if library.local_edits(name):
                    recipe_ui().message(
                        "warning",
                        f"pack {q(name)} has local edits in the library "
                        "(via an edit or init command); removing discards them",
                    )

        outcome = batch_apply(
            selected,
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
        if outcome.cancelled:
            finish(outcome)
        if dry_run:
            rows = [_pack_outcome(name, installed.get(name), action="planned") for name in selected]
        else:
            rows = [
                _pack_outcome(name, installed.get(name), action="removed")
                for name, _ in outcome.results
            ]
            rows.extend(
                _pack_outcome(
                    name,
                    installed.get(name),
                    action="partial" if isinstance(exc, PartialRemovalError) else "failed",
                    error=exc,
                )
                for name, exc in outcome.failures
            )
        rendered = render_rows(rows, fmt=fmt, columns=columns, kind="recipe.remove_outcome")
        if rendered:
            echo(rendered)
        finish(outcome)


def _pack_names(names: list[str] | None, *, stdin: bool) -> list[str]:
    """Pack names from positionals or stdin (bare names or ``recipe.pack`` records)."""
    return read_identifiers(names or [], stdin=stdin, id_field="name", accept_kinds={"recipe.pack"})


def edit_command(
    ref_text: Annotated[str, Parameter(help="Recipe name or PACK/RECIPE reference.")], /
) -> None:
    """Open an installed recipe file in $VISUAL or $EDITOR."""
    with report_config_errors():
        library = PackLibrary(library_root=library_root())
        pack, _name, recipe = _find_recipe(library, ref_text, verb="edit")
        run_editor(pack.root / recipe.path)


def edit_pack_command(name: Annotated[str, Parameter(help="Installed pack identity.")], /) -> None:
    """Open an installed pack's pyproject.toml in $VISUAL or $EDITOR."""
    with report_config_errors():
        pack = _find_pack(PackLibrary(library_root=library_root()), name)
        run_editor(pack.root / "pyproject.toml")


def edit_hook_command(
    ref_text: Annotated[str, Parameter(help="Hook name or PACK/HOOK reference.")], /
) -> None:
    """Open an installed hook module in $VISUAL or $EDITOR."""
    with report_config_errors():
        target = _find_hook(PackLibrary(library_root=library_root()), ref_text)
        if target.pack is None or target.hook is None:
            raise RecipeError(
                f"built-in hooks are engine-owned and cannot be edited: {target.name}"
            )
        run_editor(hook_module_file(target.pack.root, target.hook.module))


def _find_pack(library: PackLibrary, name: str) -> InstalledPack:
    pack = library.find_pack(name)
    if pack is None:
        raise PackNotFoundError(
            not_found("pack", name, known=sorted(pack.name for pack in library.packs()))
        )
    return pack


def _find_recipe(
    library: PackLibrary, ref_text: str, *, verb: str
) -> tuple[InstalledPack, str, RecipeEntry]:
    try:
        return find_library_recipe(library, ref_text)
    except RecipeNotFoundError as exc:
        if (noun := _other_noun(library, ref_text)) is None:
            raise
        raise RecipeNotFoundError(f"{exc}\n{hint(f'recipe {noun} {verb} {ref_text}')}") from None


def _other_noun(library: PackLibrary, ref_text: str) -> str | None:
    """``packs``/``hooks`` when a missed recipe ref names a pack or hook instead."""
    if library.find_pack(ref_text) is not None:
        return "packs"
    try:
        _find_hook(library, ref_text)
    except AmbiguousRefError:
        return "hooks"
    except ValueError:
        return None
    return "hooks"


def _find_hook(library: PackLibrary, ref_text: str) -> _ResolvedHook:
    """Resolve a hook ref: an installed pack's hook first, then a bare built-in name."""
    ref = parse_ref(ref_text)
    try:
        pack, hook = library.find_hook(ref)
    except HookNotFoundError:
        if "/" not in ref_text and ref_text in BUILTIN_HOOKS:
            return _ResolvedHook(name=ref_text)
        raise
    return _ResolvedHook(name=ref.name, pack=pack, hook=hook)


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
        "commit": pack.commit,
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
