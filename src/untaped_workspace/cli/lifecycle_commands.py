"""Lifecycle commands for workspace creation, adoption, import, and removal."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, NoReturn

from cyclopts import App, Parameter
from untaped import ProfileOverrideOption, echo, profile_override, report_errors

from untaped_workspace.application import (
    AdoptWorkspace,
    ForgetWorkspace,
    ImportWorkspace,
    InitWorkspace,
    SyncWorkspace,
    WorkspaceBootstrapper,
)
from untaped_workspace.cli.common import confirm, workspace_settings
from untaped_workspace.cli.ops_commands import print_sync_outcomes
from untaped_workspace.infrastructure import (
    GitRunner,
    LocalFilesystem,
    LocalRepoDiscoverer,
    ManifestRepository,
    WorkspaceRegistryRepository,
)

_lifecycle_parent_app: App | None = None


def register_lifecycle_commands(app: App) -> None:
    global _lifecycle_parent_app
    _lifecycle_parent_app = app
    app.command(init_command, name="init")
    app.command(adopt_command, name="adopt")
    app.command(forget_command, name="forget")


def register_import_command(app: App) -> None:
    app.command(import_command, name="import")


def init_command(
    name: Annotated[str | None, Parameter(name="", help="Workspace name.")] = None,
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
    profile: ProfileOverrideOption = None,
) -> None:
    """Initialise a new workspace named `name`.

    Default location is `<workspace.workspaces_dir>/<name>` (the
    `workspaces_dir` setting defaults to `~/.untaped/workspaces`).
    """
    if name is None:
        _show_lifecycle_help("init")
    with report_errors(), profile_override(profile):
        target = path or (workspace_settings().workspaces_dir.expanduser() / name)
        bootstrapper = WorkspaceBootstrapper(ManifestRepository(), WorkspaceRegistryRepository())
        ws = InitWorkspace(bootstrapper)(target, name=name, branch=branch)
        echo(f"initialised workspace {ws.name!r} at {ws.path}", err=True)


def adopt_command(
    path: Annotated[Path, Parameter(help="Existing directory containing already-cloned repos.")],
    *,
    name: Annotated[
        str | None,
        Parameter(name=["--name", "-n"], help="Registry name (default: dirname)."),
    ] = None,
    profile: ProfileOverrideOption = None,
) -> None:
    """Adopt existing workspace state under `path`.

    If `path` already has `untaped.yml`, validate it and register the
    workspace without rewriting the manifest. Otherwise, each immediate
    subdirectory containing `.git` is recorded in a new manifest with
    its current `origin` URL and checked-out branch.
    """
    with report_errors(), profile_override(profile):
        bootstrapper = WorkspaceBootstrapper(ManifestRepository(), WorkspaceRegistryRepository())
        result = AdoptWorkspace(
            bootstrapper,
            LocalRepoDiscoverer(GitRunner()),
            fs=LocalFilesystem(),
            warn=lambda m: echo(f"warning: {m}", err=True),
        )(path, name=name)
        ws = result.workspace
        n = len(result.repos)
        suffix = (
            " — nothing matched (use 'workspace add' to declare repos)"
            if result.discovered and n == 0
            else ""
        )
        echo(
            f"adopted workspace {ws.name!r} at {ws.path} ({n} repo{'s' if n != 1 else ''}){suffix}",
            err=True,
        )


def forget_command(
    name: Annotated[str, Parameter(help="Workspace name.")],
    *,
    prune: Annotated[
        bool,
        Parameter(
            name="--prune",
            negative="",
            help="Also delete the workspace directory (refuses if dirty).",
        ),
    ] = False,
    yes: Annotated[
        bool,
        Parameter(name=["--yes", "-y"], negative="", help="Skip the prune confirmation prompt."),
    ] = False,
    profile: ProfileOverrideOption = None,
) -> None:
    """Remove a workspace from the registry.

    The on-disk manifest and clones are preserved by default. Pass
    `--prune` to also remove the workspace directory (refused if any
    repo has uncommitted changes).
    """
    with report_errors(), profile_override(profile):
        if prune and not confirm(f"prune workspace directory for {name!r}?", yes=yes):
            echo("aborted", err=True)
            raise SystemExit(1)
        ws = ForgetWorkspace(
            WorkspaceRegistryRepository(),
            ManifestRepository(),
            fs=LocalFilesystem(),
            status=GitRunner(),
        )(name, prune=prune)
        action = "forgot and pruned" if prune else "forgot"
        echo(f"{action} workspace {ws.name!r}", err=True)


def _show_lifecycle_help(command: str) -> NoReturn:
    if _lifecycle_parent_app is not None:
        _lifecycle_parent_app.help_print([command])
    raise SystemExit()


def import_command(
    source: Annotated[Path, Parameter(help="Path to a YAML manifest.")],
    dest: Annotated[Path, Parameter(help="Destination workspace directory.")],
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
            help="Clone the imported repos immediately (only the repos in <source>).",
        ),
    ] = False,
    profile: ProfileOverrideOption = None,
) -> None:
    """Adopt a workspace from a local YAML manifest."""
    with report_errors(), profile_override(profile):
        manifests = ManifestRepository()
        bootstrapper = WorkspaceBootstrapper(manifests, WorkspaceRegistryRepository())
        result = ImportWorkspace(manifests, bootstrapper)(source, path=dest, name=name)
        ws = result.workspace
        echo(f"imported workspace {ws.name!r} at {ws.path}", err=True)
        if sync:
            outcomes = SyncWorkspace(
                ManifestRepository(),
                GitRunner(),
                fs=LocalFilesystem(),
                cache_dir=workspace_settings().cache_dir,
            )(ws, only=result.repos)
            print_sync_outcomes(outcomes, fmt="table", columns=None)
