"""Library commands: recipe ``list``/``get``/``edit``/``validate``, ``packs …`` and hook reads."""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from untaped.sdk import (
    ColumnsOption,
    Diagnostics,
    DryRunOption,
    Failure,
    FormatOption,
    OutputFormat,
    StdinOption,
    UntapedError,
    UsageError,
    YesOption,
    batch_apply,
    emit,
    finish,
    hint,
    not_found,
    plural,
    q,
    read_identifiers,
    run_editor,
    ui_context,
    writes,
)
from untaped_recipe.application.check_pack import check_library, check_ref
from untaped_recipe.application.resolution import find_library_recipe
from untaped_recipe.builtins.registry import BUILTIN_HOOKS
from untaped_recipe.cli.common import (
    as_recipe_error,
    library_dir,
    report_config_errors,
)
from untaped_recipe.cli.detail import (
    hook_record,
    pack_record,
    recipe_record,
    table_recipe_detail,
)
from untaped_recipe.domain.hook_project import hook_module_file
from untaped_recipe.domain.pack import (
    HookEntry,
    InstalledPack,
    PackManifest,
    RecipeEntry,
    parse_ref,
)
from untaped_recipe.errors import (
    AmbiguousRefError,
    HookNotFoundError,
    LocalChangesError,
    PackNotFoundError,
    PartialRemovalError,
    PathNotFoundError,
    RecipeError,
    RecipeNotFoundError,
)
from untaped_recipe.infrastructure.pack_files import read_pack_manifest
from untaped_recipe.infrastructure.pack_inspector import PackInspector
from untaped_recipe.infrastructure.pack_store import (
    PackLibrary,
    changed_hook_files,
    checkout_commit,
    fetch_pack_source,
    is_git_url,
    local_edits_message,
    pack_content_hash,
    validate_pack,
)
from untaped_recipe.records import (
    AddOutcome,
    Hook,
    InstalledRecipe,
    Pack,
    RemoveOutcome,
    SyncOutcome,
)

_diag = Diagnostics()

_EMPTY_LIBRARY_HINT = (
    "no packs installed; scaffold one with `untaped recipe packs init NAME` "
    "or install one with `untaped recipe packs add PATH|GIT_URL`"
)


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


@writes
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
    copy with local edits) replaces an installed pack. The pack's recipes and
    hooks are listed on stderr. A pack must load, contain a `uv.lock`, and
    contain no symlinks (ignored directories such as `.venv` excepted).

    --name sets the installed name, the pack's identity everywhere. A local
    source is recorded as an absolute path; a git source records the
    requested --rev and the commit it resolved to. A git fetch that times out
    exits 5. Installing a pack installs code that runs as you:
    `untaped agents recipe trust`.

    Examples
    --------
    ```console
    $ untaped recipe packs add https://git.example.com/acme/untaped-recipes.git --rev v1.2.0
    ```
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
        library = PackLibrary(library_dir=library_dir())
        edited = force and library.local_edits(installed_name)
        if edited and not discard_edits:
            raise LocalChangesError(local_edits_message(installed_name))
        _render_pack_add_preview(installed_name, manifest, local_edits=edited)
        added = library.add(
            source_dir,
            source=source,
            rev=rev,
            commit=commit,
            name=name,
            force=force,
            discard_edits=discard_edits,
        )
        record = AddOutcome(
            name=installed_name,
            action="updated" if added.replaced else "created",
            source=source,
            rev=rev,
            commit=commit,
        )
        emit(record, fmt=fmt, columns=columns)


@writes
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

    A branch or tag rev moves forward. Packs whose content would change are
    listed and confirmed first, with the commit move (`old -> new`) and the
    hook-code files that change (`src/`, root `*.py`, `pyproject.toml`,
    `uv.lock`, `uv.toml`, `.python-version`, `setup.cfg`): the code that runs
    next. The rest report `unchanged`.

    A pack whose library copy has local edits fails unless --discard-edits.
    A pack another command replaced or removed while sync fetched it fails
    and is left as that command made it.
    A failed pack gets a `failed` row and the others still sync; the run
    exits 1, or 5 when a fetch timed out.
    """
    with report_config_errors(), tempfile.TemporaryDirectory() as temp_root:
        library = PackLibrary(library_dir=library_dir())
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
                _diag.failure(exc, item=name)
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
            describe=lambda plan: _sync_row(plan, action="planned").model_dump(mode="json"),
            ui=ui_context(strict=False),
            destructive=True,
            assume_yes=yes,
            preview_only=dry_run,
            preview=lambda _rows: _sync_preview(changed),
        )
        if outcome.cancelled:
            finish(outcome)
        if not dry_run:
            # An unchanged pack still moved to the fetched commit: a commit
            # may touch only files the install ignores.
            @as_recipe_error
            def record_commit(plan: _SyncPlan) -> None:
                library.record_commit(plan.pack.name, plan.commit or "", expect=plan.pack)

            for plan in plans:
                if not plan.changed and plan.commit and plan.commit != plan.pack.commit:
                    try:
                        record_commit(plan)
                    except UntapedError as exc:
                        _diag.failure(exc, item=plan.pack.name)
                        failed[plan.pack.name] = exc
        failed.update((plan.pack.name, exc) for plan, exc in outcome.failures)
        by_name = {plan.pack.name: plan for plan in plans}
        rows = [
            _pack_outcome(SyncOutcome, name, pack, action="failed", error=failed[name])
            if name in failed
            else _sync_row(by_name[name], action=_sync_action(by_name[name], dry_run=dry_run))
            for name, pack in selected.items()
        ]
        emit(rows, fmt=fmt, columns=columns)
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
        expect=plan.pack,
    )


def _sync_preview(plans: Sequence[_SyncPlan]) -> None:
    """List the packs about to sync: source, commit move, and changed hook code."""
    _diag.info(f"About to sync {plural(len(plans), 'pack')}:", unmuted=True)
    for plan in plans:
        pack = plan.pack
        at = f"@{pack.rev}" if pack.rev else ""
        move = (
            f" ({_short_commit(pack.commit)} -> {_short_commit(plan.commit)})"
            if plan.commit
            else ""
        )
        _diag.info(f"  - {pack.name} from {pack.source}{at}{move}", unmuted=True)
        if plan.hook_changes:
            _diag.info(f"    hook code changed: {', '.join(plan.hook_changes)}", unmuted=True)
        else:
            _diag.info("    hook code unchanged", unmuted=True)


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


def _sync_row(plan: _SyncPlan, *, action: str) -> SyncOutcome:
    return SyncOutcome(
        name=plan.pack.name,
        action=action,
        source=plan.pack.source,
        rev=plan.pack.rev or None,
        commit=plan.commit,
    )


def _pack_outcome[T: (SyncOutcome, RemoveOutcome)](
    model: type[T],
    name: str,
    pack: InstalledPack | None,
    *,
    action: str,
    error: UntapedError | None = None,
) -> T:
    """A row with ``pack``'s recorded source, rev and commit (none for an unloadable pack).

    A failed row's ``error`` was already reported (and counted) on stderr.
    """
    info = None if error is None else Failure.from_exception(error)
    return model(
        name=name,
        action=action,
        source=(pack.source or None) if pack else None,
        rev=(pack.rev or None) if pack else None,
        commit=(pack.commit or None) if pack else None,
        detail=None if info is None else info.message,
        error=info,
    )


def list_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """List installed recipes.

    Recipes come from installed packs, so the list is empty until one is
    installed (`untaped recipe packs add`). A pack whose `pyproject.toml`
    doesn't parse is skipped with a warning, here and when resolving refs,
    unless named explicitly.
    """
    _list_rows(
        lambda installed: [
            recipe_record(pack, name, detail=False)
            for pack in installed
            for name in sorted(pack.manifest.recipes)
        ],
        fmt=fmt,
        columns=columns,
    )


def list_packs_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """List installed packs."""
    _list_rows(
        lambda installed: [pack_record(pack, detail=False) for pack in installed],
        fmt=fmt,
        columns=columns,
    )


def list_hooks_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """List installed hooks, then the built-in ones.

    A built-in such as `yaml_edit` is listed with pack `(builtin)`.
    """
    _list_rows(
        lambda installed: [
            *(
                hook_record(pack, name, detail=False)
                for pack in installed
                for name in sorted(pack.manifest.hooks)
            ),
            *(hook_record(None, name, detail=False) for name in sorted(BUILTIN_HOOKS)),
        ],
        fmt=fmt,
        columns=columns,
        hint_when_empty=not BUILTIN_HOOKS,
    )


def _list_rows(
    rows_of: Callable[[list[InstalledPack]], Sequence[Pack | InstalledRecipe | Hook]],
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
    hint_when_empty: bool = True,
) -> None:
    """Emit one row kind for every loadable installed pack, warning about the rest."""
    with report_config_errors():
        library = PackLibrary(library_dir=library_dir())
        installed = library.packs()
        for name, error in library.load_errors().items():
            _diag.warning(f"skipping pack '{name}': {error}")
        emit(rows_of(installed), fmt=fmt, columns=columns)
        # The hint is human guidance: structured formats stay machine-clean.
        if fmt == "table" and not installed and hint_when_empty:
            _diag.info(_EMPTY_LIBRARY_HINT)


def get_command(
    ref_text: Annotated[str, Parameter(help="Recipe name or PACK/RECIPE reference.")],
    /,
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Show an installed recipe."""
    with report_config_errors():
        library = PackLibrary(library_dir=library_dir())
        pack, name, _entry = _find_recipe(library, ref_text, verb="get")
        detail = recipe_record(pack, name, detail=True)
        # A table shows the steps as one line, so it is a view, not the record.
        emit(
            table_recipe_detail(detail) if fmt == "table" else detail,
            fmt=fmt,
            columns=columns,
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
        pack = _find_pack(PackLibrary(library_dir=library_dir()), name)
        emit(pack_record(pack, detail=True), fmt=fmt, columns=columns)


def get_hook_command(
    ref_text: Annotated[str, Parameter(help="Hook name, PACK/HOOK reference, or built-in.")],
    /,
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Show an installed or built-in hook."""
    with report_config_errors():
        target = _find_hook(PackLibrary(library_dir=library_dir()), ref_text)
        detail = hook_record(target.pack, target.name, detail=True)
        emit(detail, fmt=fmt, columns=columns)


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
    """Validate a pack, recipe, or the whole installed library.

    With no argument it checks every installed pack and `packs.toml`; with a
    ref or path, one pack, recipe or built-in hook. It scans hook modules
    without importing them. A pack that declares
    hooks needs a `uv.lock` that `uv lock --check` accepts. Every
    `packs.toml` row must carry its `content_hash`; a malformed or
    incomplete row blocks every library change until it is fixed. An
    installed pack whose `pyproject.toml` doesn't parse fails. Template and
    copy sources containing `{{ input }}` tokens are checked only up to their
    literal directory prefix. Any `fail` row exits 1.
    """
    with report_config_errors():
        root = library_dir()
        library = PackLibrary(library_dir=root)
        inspector = PackInspector(library_dir=root)
        rows = (
            check_library(library=library, inspector=inspector)
            if ref_text is None
            else [check_ref(ref_text, library=library, inspector=inspector)]
        )
        emit(rows, fmt=fmt, columns=columns)
        if ref_text is None and not rows:
            _diag.info(_EMPTY_LIBRARY_HINT)
        finish(any(row.status == "fail" for row in rows))


@writes(destructive=True)
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
    """Remove installed packs.

    Warns when a library copy has local edits. A `partial` row means removal
    stopped partway; running it again finishes it.
    """
    with report_config_errors():
        library = PackLibrary(library_dir=library_dir())
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
            _diag.info(f"About to remove {plural(len(rows), 'pack')}:", unmuted=True)
            for row in rows:
                _diag.info(f"  - {row['name']}", unmuted=True)
            for name in selected:
                if library.local_edits(name):
                    _diag.warning(
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
            ui=ui_context(strict=False),
            destructive=True,
            assume_yes=yes,
            preview_only=dry_run,
            preview=_preview,
        )
        if outcome.cancelled:
            finish(outcome)
        if dry_run:
            rows = [
                _pack_outcome(RemoveOutcome, name, installed.get(name), action="planned")
                for name in selected
            ]
        else:
            rows = [
                _pack_outcome(RemoveOutcome, name, installed.get(name), action="removed")
                for name, _ in outcome.results
            ]
            rows.extend(
                _pack_outcome(
                    RemoveOutcome,
                    name,
                    installed.get(name),
                    action="partial" if isinstance(exc, PartialRemovalError) else "failed",
                    error=exc,
                )
                for name, exc in outcome.failures
            )
        emit(rows, fmt=fmt, columns=columns)
        finish(outcome)


def _pack_names(names: list[str] | None, *, stdin: bool) -> list[str]:
    """Pack names from positionals or stdin (bare names or ``recipe.pack`` records)."""
    return read_identifiers(names or [], stdin=stdin, id_field="name", accepts=(Pack,))


def edit_command(
    ref_text: Annotated[str, Parameter(help="Recipe name or PACK/RECIPE reference.")], /
) -> None:
    """Open an installed recipe file in $VISUAL or $EDITOR."""
    with report_config_errors():
        library = PackLibrary(library_dir=library_dir())
        pack, _name, recipe = _find_recipe(library, ref_text, verb="edit")
        run_editor(pack.root / recipe.path)


def edit_pack_command(name: Annotated[str, Parameter(help="Installed pack identity.")], /) -> None:
    """Open an installed pack's pyproject.toml in $VISUAL or $EDITOR."""
    with report_config_errors():
        pack = _find_pack(PackLibrary(library_dir=library_dir()), name)
        run_editor(pack.root / "pyproject.toml")


def edit_hook_command(
    ref_text: Annotated[str, Parameter(help="Hook name or PACK/HOOK reference.")], /
) -> None:
    """Open an installed hook module in $VISUAL or $EDITOR.

    Built-in hooks can't be edited.
    """
    with report_config_errors():
        target = _find_hook(PackLibrary(library_dir=library_dir()), ref_text)
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


def _render_pack_add_preview(
    installed_name: str,
    manifest: PackManifest,
    *,
    local_edits: bool = False,
) -> None:
    recipes = ", ".join(sorted(manifest.recipes)) or "(none)"
    hooks = ", ".join(sorted(manifest.hooks)) or "(none)"
    _diag.info(f"Pack: {installed_name}")
    _diag.info(f"Recipes: {recipes}")
    _diag.info(f"Hooks: {hooks}")
    if local_edits:
        _diag.warning("library copy has local edits; --discard-edits will overwrite them")
