"""``untaped recipe packs ...``: install, sync, inspect, scaffold and remove recipe packs."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from untaped.sdk import (
    Confirm,
    Failure,
    Group,
    Output,
    Positional,
    Stdin,
    UntapedError,
    UsageError,
    not_found,
    plural,
    q,
    run_editor,
)
from untaped_recipe.commands._library import NO_LOCK_NOTE, find_pack
from untaped_recipe.commands._recipe import RecipeCommand, as_recipe_error, recipe_errors
from untaped_recipe.commands.detail import pack_record
from untaped_recipe.commands.library import LOCK
from untaped_recipe.domain.pack import InstalledPack, PackManifest
from untaped_recipe.domain.paths import safe_library_name
from untaped_recipe.errors import (
    LocalChangesError,
    PackNotFoundError,
    PartialRemovalError,
    PathNotFoundError,
)
from untaped_recipe.infrastructure import pack_scaffold
from untaped_recipe.infrastructure.pack_files import read_pack_manifest
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
from untaped_recipe.records import AddOutcome, Pack, RemoveOutcome, SyncOutcome

#: How a pack name reads in help.
PACK = "Installed pack name."

#: How ``--discard-edits`` reads in help.
DISCARD_EDITS = "Overwrite local edits made to the library copy."


@dataclass(frozen=True)
class _SyncPlan:
    """An installed pack and its freshly fetched source tree (at ``commit`` for git)."""

    pack: InstalledPack
    source_dir: Path
    changed: bool
    commit: str | None = None
    hook_changes: tuple[str, ...] = ()


class List(RecipeCommand):
    """List installed packs."""

    output: Output[Pack]

    def run(self) -> Iterator[Pack]:
        with recipe_errors():
            installed = self.installed()
        if not installed:
            self.empty_hint()
        return iter([pack_record(pack, detail=False) for pack in installed])


class Get(RecipeCommand):
    """Show an installed pack: its source, recipes and hooks."""

    name: Positional[str] = Field(description=PACK)
    output: Output[Pack]

    def run(self) -> Pack:
        with recipe_errors():
            return pack_record(find_pack(self.library(), self.name), detail=True)


class Edit(RecipeCommand):
    """Open an installed pack's pyproject.toml in $VISUAL or $EDITOR."""

    name: Positional[str] = Field(description=PACK)

    def run(self) -> None:
        with recipe_errors():
            run_editor(find_pack(self.library(), self.name).root / "pyproject.toml")


class Init(RecipeCommand):
    """Scaffold a recipe pack project under the current directory and print its path.

    A pack is a Python project: its `pyproject.toml` lists recipes under
    `[tool.untaped_recipe.recipes]` and hooks under
    `[tool.untaped_recipe.hooks]`. `untaped[recipe]` stays a dev-only
    dependency; hook runtime dependencies go in `[project].dependencies`.
    Packs ship `pytest` with `pythonpath = ["src"]`, so
    `uv run --project PACK pytest` works at once.

    Each `init` refreshes the pack's `uv.lock`, which needs package-index
    access (or a `[tool.uv.sources]` override). If locking fails, the files
    stay and the error says how to repair them. With --no-lock, hooks can't
    run until `uv lock` succeeds: they run with `uv run --locked --no-dev`.

    Examples
    --------
    ```console
    $ untaped recipe packs init acme
    $ untaped recipe init ./acme/editorconfig
    $ untaped recipe hooks init ./acme/pin_python --kind validate
    $ untaped recipe test ./acme
    ```
    """

    name: Positional[str] = Field(description="Pack NAME, created under the current directory.")
    lock: bool = Field(True, description=LOCK)

    def run(self) -> str:
        with recipe_errors():
            pack_name = safe_library_name(self.name, field="pack")
            path = pack_scaffold.scaffold_pack(Path.cwd() / pack_name, pack_name, lock=self.lock)
        if not self.lock:
            self.ctx.diag.warning(NO_LOCK_NOTE.format(path=path))
        return str(path)


class Add(RecipeCommand):
    """Install a recipe pack from a path or git URL.

    Installing never prompts: only --force (and --discard-edits for a library
    copy with local edits) replaces an installed pack. The pack's recipes and
    hooks are listed on stderr. A pack must load, contain a `uv.lock`, and
    contain no symlinks (ignored directories such as `.venv` excepted).

    --name sets the installed name, the pack's identity everywhere. A local
    source is recorded as an absolute path; a git source records the
    requested --rev and the commit it resolved to. A git fetch that times out
    exits 5. Installing a pack installs code that runs as you: `untaped docs
    recipe guides hooks`.

    Examples
    --------
    ```console
    $ untaped recipe packs add https://git.example.com/acme/untaped-recipes.git --rev v1.2.0
    ```
    """

    source: Positional[str] = Field(description="Pack project path or git URL.")
    rev: str | None = Field(None, description="Git revision to install.")
    name: str | None = Field(None, description="Installed pack name, instead of the manifest's.")
    force: bool = Field(False, description="Replace an existing installed pack.")
    discard_edits: bool = Field(
        False, description="With --force, overwrite local edits made to the library copy."
    )
    output: Output[AddOutcome]

    def run(self) -> AddOutcome:
        git = is_git_url(self.source)
        if self.rev is not None and not git:
            raise UsageError("--rev is only valid for git URL sources")
        # Record where the pack came from, not where the shell happened to be.
        source = self.source if git else str(Path(self.source).expanduser().absolute())
        with recipe_errors(), tempfile.TemporaryDirectory() as temp_root:
            source_dir = (
                fetch_pack_source(source, rev=self.rev, dest=Path(temp_root) / "pack")
                if git
                else Path(source)
            )
            commit = checkout_commit(source_dir) if git else None
            manifest = read_pack_manifest(source_dir)
            # Validate before the pack summary: error output leads, and the
            # summary follows only on a pack that will actually install.
            validate_pack(source_dir, manifest)
            installed_name = self.name or manifest.name
            library = self.library()
            edited = self.force and library.local_edits(installed_name)
            if edited and not self.discard_edits:
                raise LocalChangesError(local_edits_message(installed_name))
            self._summary(installed_name, manifest, local_edits=edited)
            added = library.add(
                source_dir,
                source=source,
                rev=self.rev,
                commit=commit,
                name=self.name,
                force=self.force,
                discard_edits=self.discard_edits,
            )
        return AddOutcome(
            name=installed_name,
            action="updated" if added.replaced else "created",
            source=source,
            rev=self.rev,
            commit=commit,
        )

    def _summary(self, name: str, manifest: PackManifest, *, local_edits: bool) -> None:
        diag = self.ctx.diag
        diag.info(f"Pack: {name}")
        diag.info(f"Recipes: {', '.join(sorted(manifest.recipes)) or '(none)'}")
        diag.info(f"Hooks: {', '.join(sorted(manifest.hooks)) or '(none)'}")
        if local_edits:
            diag.warning("library copy has local edits; --discard-edits will overwrite them")


class Sync(RecipeCommand):
    """Re-fetch installed packs from their recorded source and rev.

    A branch or tag rev moves forward. Packs whose content would change are
    listed and confirmed first, with the commit move (`old -> new`) and the
    hook-code files that change (`src/`, root `*.py`, `pyproject.toml`,
    `uv.lock`, `uv.toml`, `.python-version`, `setup.cfg`): the code that runs
    next. The rest report `unchanged`.

    A pack whose library copy has local edits fails unless --discard-edits.
    A pack another command replaced or removed while sync fetched it fails
    and is left as that command made it. A failed pack gets a `failed` row
    and the others still sync; the run exits 1, or 5 when a fetch timed out.
    """

    names: Positional[list[str]] = Field(
        default_factory=list, description="Installed pack names (or pass --all)."
    )
    all_: bool = Field(False, description="Sync every installed pack.")
    stdin: Stdin[Pack]
    discard_edits: bool = Field(False, description=DISCARD_EDITS)
    confirm: Confirm
    output: Output[SyncOutcome]

    def run(self) -> Iterator[SyncOutcome]:
        with recipe_errors(), tempfile.TemporaryDirectory() as temp_root:
            library = self.library()
            selected = self._selected(library)
            plans: list[_SyncPlan] = []
            failed: dict[str, UntapedError] = {}

            def fetch_one(pack: InstalledPack) -> _SyncPlan:
                return _fetch(library, pack, Path(temp_root), discard=self.discard_edits)

            fetch = as_recipe_error(fetch_one)
            for name, pack in selected.items():
                try:
                    plans.append(fetch(pack))
                except UntapedError as exc:
                    self.ctx.diag.failure(exc, item=name)
                    failed[name] = exc
            changed = [plan for plan in plans if plan.changed]
            go = self.confirm(
                f"Sync {plural(len(changed), 'pack')}?",
                preview=lambda: _sync_lines(changed),
                needed=bool(changed),
            )
            if go:
                failed |= self._install(library, plans)
        by_name = {plan.pack.name: plan for plan in plans}
        for name, pack in selected.items():
            if name in failed:
                yield _outcome(SyncOutcome, name, pack, action="failed", error=failed[name])
            else:
                plan = by_name[name]
                action = "unchanged" if not plan.changed else "updated" if go else "planned"
                yield SyncOutcome(
                    name=pack.name,
                    action=action,
                    source=pack.source,
                    rev=pack.rev or None,
                    commit=plan.commit,
                )

    def _selected(self, library: PackLibrary) -> dict[str, InstalledPack]:
        """The installed packs to sync, by name: the named ones, or every one with ``--all``."""
        names = self.stdin.identifiers() if self.stdin.requested else self.names
        if bool(names) == self.all_:
            raise UsageError("name the packs to sync, or pass --all (not both)")
        installed = {pack.name: pack for pack in library.packs()}
        if self.all_:
            return {name: installed[name] for name in sorted(installed)}
        for name in names:
            if name not in installed:
                raise PackNotFoundError(not_found("pack", name, known=sorted(installed)))
        return {name: installed[name] for name in names}

    def _install(self, library: PackLibrary, plans: list[_SyncPlan]) -> dict[str, UntapedError]:
        """Install each changed pack; an unchanged one still records its fetched commit.

        A commit may touch only files the install ignores, so an unchanged pack
        moves to it too. Returns the packs that failed.
        """
        failed: dict[str, UntapedError] = {}

        @as_recipe_error
        def install(plan: _SyncPlan) -> None:
            if plan.changed:
                library.add(
                    plan.source_dir,
                    source=plan.pack.source,
                    rev=plan.pack.rev or None,
                    commit=plan.commit,
                    name=plan.pack.name,
                    force=True,
                    discard_edits=self.discard_edits,
                    expect=plan.pack,
                )
            elif plan.commit and plan.commit != plan.pack.commit:
                library.record_commit(plan.pack.name, plan.commit, expect=plan.pack)

        for plan in plans:
            try:
                install(plan)
            except UntapedError as exc:
                self.ctx.diag.failure(exc, item=plan.pack.name)
                failed[plan.pack.name] = exc
        return failed


class Remove(RecipeCommand):
    """Remove installed packs.

    Warns when a library copy has local edits. A `partial` row means removal
    stopped partway; running it again finishes it.
    """

    names: Positional[list[str]] = Field(default_factory=list, description="Installed pack names.")
    stdin: Stdin[Pack]
    confirm: Confirm
    output: Output[RemoveOutcome]

    def run(self) -> Iterator[RemoveOutcome]:
        with recipe_errors():
            library = self.library()
            selected = self.stdin.identifiers(self.names)
            installed = {pack.name: pack for pack in library.packs()}
            # A removal that stopped partway leaves a name only reconcile() still sees.
            known = set(installed) | set(library.load_errors()) | set(library.reconcile())
            for name in selected:
                if name not in known:
                    raise PackNotFoundError(not_found("pack", name, known=sorted(known)))
            # Whoever passed --yes already chose; the warning is for whoever is asked.
            edited = [] if self.confirm.yes else [n for n in selected if library.local_edits(n)]
            for name in edited:
                self.ctx.diag.warning(
                    f"pack {q(name)} has local edits in the library "
                    "(via an edit or init command); removing discards them",
                )
            lines = [f"About to remove {plural(len(selected), 'pack')}:"]
            lines += [f"  - {name}" for name in selected]
            if not self.confirm(f"Remove {plural(len(selected), 'pack')}?", preview=lambda: lines):
                return iter(
                    [
                        _outcome(RemoveOutcome, name, installed.get(name), action="planned")
                        for name in selected
                    ]
                )
            rows: list[RemoveOutcome] = []
            remove = as_recipe_error(library.remove)
            for name in selected:
                try:
                    remove(name)
                except UntapedError as exc:
                    self.ctx.diag.failure(exc, item=name)
                    action = "partial" if isinstance(exc, PartialRemovalError) else "failed"
                    rows.append(
                        _outcome(RemoveOutcome, name, installed.get(name), action=action, error=exc)
                    )
                else:
                    rows.append(
                        _outcome(RemoveOutcome, name, installed.get(name), action="removed")
                    )
        return iter(rows)


def _fetch(
    library: PackLibrary, pack: InstalledPack, temp_root: Path, *, discard: bool
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
            # Resolving a path recorded as typed against today's working
            # directory could install a different pack.
            raise ValueError(
                f"recorded source {q(pack.source)} is a relative path; "
                "reinstall the pack with `untaped recipe packs add --force`"
            )
        if not source_dir.is_dir():
            raise PathNotFoundError(f"pack source not found: {pack.source}")
    validate_pack(source_dir, read_pack_manifest(source_dir))
    changed = pack_content_hash(source_dir) != pack_content_hash(pack.root)
    if changed and not discard and library.local_edits(pack.name):
        raise LocalChangesError(local_edits_message(pack.name))
    hook_changes = tuple(changed_hook_files(pack.root, source_dir)) if changed else ()
    return _SyncPlan(
        pack=pack, source_dir=source_dir, changed=changed, commit=commit, hook_changes=hook_changes
    )


def _sync_lines(plans: list[_SyncPlan]) -> list[str]:
    """The packs about to sync: source, commit move, and changed hook code."""
    lines = [f"About to sync {plural(len(plans), 'pack')}:"]
    for plan in plans:
        pack = plan.pack
        at = f"@{pack.rev}" if pack.rev else ""
        move = f" ({_short(pack.commit)} -> {_short(plan.commit)})" if plan.commit else ""
        lines.append(f"  - {pack.name} from {pack.source}{at}{move}")
        changed = ", ".join(plan.hook_changes)
        lines.append(f"    hook code changed: {changed}" if changed else "    hook code unchanged")
    return lines


def _short(commit: str | None) -> str:
    return commit[:12] if commit else "unrecorded"


def _outcome[T: (SyncOutcome, RemoveOutcome)](
    model: type[T],
    name: str,
    pack: InstalledPack | None,
    *,
    action: str,
    error: UntapedError | None = None,
) -> T:
    """A row with ``pack``'s recorded source, rev and commit (none for an unloadable pack)."""
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


class PacksGroup(Group):
    """Install, sync, inspect, and scaffold recipe packs."""

    commands = (List, Get, Edit, Init, Add, Sync, Remove)
