"""Lifecycle commands for workspace creation, adoption, import, and removal."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from untaped.capabilities.workspace.application import (
    AdoptWorkspace,
    ForgetWorkspace,
    ImportWorkspace,
    InitWorkspace,
    SyncWorkspace,
    WorkspaceBootstrapper,
)
from untaped.capabilities.workspace.cli.common import record_row, workspace_settings
from untaped.capabilities.workspace.cli.ops_commands import (
    any_sync_failed,
    print_sync_outcomes,
)
from untaped.capabilities.workspace.domain import WorkspaceOutcome
from untaped.capabilities.workspace.infrastructure import (
    GitRunner,
    LocalFilesystem,
    LocalRepoDiscoverer,
    WorkspaceRegistryRepository,
    YamlManifestRepository,
)
from untaped.capability_api import (
    ColumnsOption,
    FormatOption,
    UntapedError,
    YesOption,
    batch_apply,
    emit,
    finish,
    plural,
    q,
    report_errors,
    ui_context,
)


def register_lifecycle_commands(app: App) -> None:
    app.command(init_command, name="init")
    app.command(adopt_command, name="adopt")
    app.command(forget_command, name="forget")


def register_import_command(app: App) -> None:
    app.command(import_command, name="import")


def init_command(
    name: Annotated[str, Parameter(help="Workspace name.")],
    /,
    *,
    path: Annotated[
        Path | None,
        Parameter(
            name=["--path", "-p"],
            help="Override location (default: workspace.workspaces_dir / name).",
        ),
    ] = None,
    branch: Annotated[
        str | None,
        Parameter(name=["--branch", "-b"], help="Default branch for newly cloned repos."),
    ] = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Initialize a new workspace named `name`.

    Default location is `<workspace.workspaces_dir>/<name>` (the
    `workspaces_dir` setting defaults to `~/.untaped/workspaces`).
    """
    with report_errors():
        target = path or (workspace_settings().workspaces_dir.expanduser() / name)
        bootstrapper = WorkspaceBootstrapper(
            YamlManifestRepository(), WorkspaceRegistryRepository()
        )
        ws = InitWorkspace(bootstrapper)(target, name=name, branch=branch)
        ui_context(strict=False).success(f"initialized workspace {q(ws.name)} at {ws.path}")
        row = WorkspaceOutcome(name=ws.name, action="created", target_path=ws.path)
        emit([record_row(row)], fmt=fmt, columns=columns, kind="workspace.init_outcome")


def adopt_command(
    path: Annotated[Path, Parameter(help="Existing directory containing already-cloned repos.")],
    /,
    *,
    name: Annotated[
        str | None,
        Parameter(name=["--name", "-n"], help="Registry name (default: dirname)."),
    ] = None,
) -> None:
    """Adopt existing workspace state under `path`.

    If `path` already has `untaped.yml`, validate it and register the
    workspace without rewriting the manifest. Otherwise, each immediate
    subdirectory containing `.git` is recorded in a new manifest with
    its current `origin` URL and checked-out branch.
    """
    with report_errors():
        ui = ui_context(strict=False)
        bootstrapper = WorkspaceBootstrapper(
            YamlManifestRepository(), WorkspaceRegistryRepository()
        )
        result = AdoptWorkspace(
            bootstrapper,
            LocalRepoDiscoverer(GitRunner()),
            fs=LocalFilesystem(),
            warn=lambda m: ui.message("warning", m),
        )(path, name=name)
        ws = result.workspace
        n = len(result.repos)
        suffix = (
            " — nothing matched (use 'workspace add' to declare repos)"
            if result.discovered and n == 0
            else ""
        )
        ui.success(f"adopted workspace {q(ws.name)} at {ws.path} ({plural(n, 'repo')}){suffix}")


def forget_command(
    name: Annotated[str, Parameter(help="Workspace name.")],
    /,
    *,
    prune: Annotated[
        bool,
        Parameter(
            name="--prune",
            negative="",
            help=(
                "Also delete managed clones and untaped.yml, then the workspace "
                "directory if nothing else is left (refuses unsafe local state)."
            ),
        ),
    ] = False,
    yes: YesOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Remove a workspace from the registry.

    The on-disk manifest and clones are preserved by default. Pass
    `--prune` to also delete declared and orphan clones plus `untaped.yml`
    (refused if any clone that would be deleted has unsafe local state).
    Other files are kept; the workspace directory is removed only when
    it ends up empty.
    """
    with report_errors():
        ui = ui_context(strict=False)
        registry = WorkspaceRegistryRepository()
        forget_workspace = ForgetWorkspace(
            registry,
            YamlManifestRepository(),
            fs=LocalFilesystem(),
            prune_safety=GitRunner(),
            warn=lambda m: ui.message("warning", m),
        )

        def _describe(workspace_name: str) -> dict[str, object]:
            # The confirmation preview must show *where* files will be
            # deleted, not just the registry name.
            try:
                location = str(registry.get(workspace_name).path)
            except UntapedError:
                location = "(not registered)"
            return {"workspace": workspace_name, "path": location}

        def _forget_one(workspace_name: str) -> WorkspaceOutcome:
            ws = forget_workspace(workspace_name, prune=prune)
            action = "forgot and pruned" if prune else "forgot"
            ui.success(f"{action} workspace {q(ws.name)}")
            return WorkspaceOutcome(
                name=ws.name,
                action="pruned" if prune else "forgotten",
                target_path=ws.path,
            )

        outcome = batch_apply(
            [name],
            _forget_one,
            verb="forget",
            noun="workspace",
            label=lambda workspace_name: workspace_name,
            describe=_describe,
            ui=ui,
            destructive=prune,
            assume_yes=yes,
        )
        if outcome.results:
            emit(
                [record_row(row) for _, row in outcome.results],
                fmt=fmt,
                columns=columns,
                kind="workspace.forget_outcome",
            )
    finish(outcome)


def import_command(
    source: Annotated[Path, Parameter(help="Path to a YAML manifest.")],
    dest: Annotated[Path, Parameter(help="Destination workspace directory.")],
    /,
    *,
    name: Annotated[
        str | None,
        Parameter(name=["--name", "-n"], help="Registry name override."),
    ] = None,
    sync: Annotated[
        bool,
        Parameter(
            name="--sync",
            negative="",
            help="Clone the imported repos immediately (only the repos in SOURCE).",
        ),
    ] = False,
) -> None:
    """Adopt a workspace from a local YAML manifest."""
    with report_errors():
        manifests = YamlManifestRepository()
        bootstrapper = WorkspaceBootstrapper(manifests, WorkspaceRegistryRepository())
        result = ImportWorkspace(manifests, bootstrapper)(source, path=dest, name=name)
        ws = result.workspace
        ui_context(strict=False).success(f"imported workspace {q(ws.name)} at {ws.path}")
        if sync:
            outcomes = SyncWorkspace(
                YamlManifestRepository(),
                GitRunner(),
                fs=LocalFilesystem(),
                cache_dir=workspace_settings().cache_dir,
            )(ws, only=result.repos)
            print_sync_outcomes(outcomes, fmt="table", columns=None)
            finish(any_sync_failed(outcomes))
