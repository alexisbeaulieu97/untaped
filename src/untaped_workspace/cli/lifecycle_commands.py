"""Lifecycle commands for workspace creation, adoption, import, and removal."""

from __future__ import annotations

from pathlib import Path

import typer
from untaped import ProfileOverrideOption, profile_override, report_errors

from untaped_workspace.application import (
    AdoptWorkspace,
    ForgetWorkspace,
    ImportWorkspace,
    InitWorkspace,
    SyncWorkspace,
    WorkspaceBootstrapper,
)
from untaped_workspace.cli.common import confirm, workspace_settings
from untaped_workspace.cli.completions import complete_workspace_name
from untaped_workspace.cli.ops_commands import print_sync_outcomes
from untaped_workspace.infrastructure import (
    GitRunner,
    LocalFilesystem,
    LocalRepoDiscoverer,
    ManifestRepository,
    WorkspaceRegistryRepository,
)


def register_lifecycle_commands(app: typer.Typer) -> None:
    app.command("init", no_args_is_help=True)(init_command)
    app.command("adopt", no_args_is_help=True)(adopt_command)
    app.command("forget", no_args_is_help=True)(forget_command)


def register_import_command(app: typer.Typer) -> None:
    app.command("import", no_args_is_help=True)(import_command)


def init_command(
    name: str = typer.Argument(..., help="Workspace name."),
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Override location (default: workspace.workspaces_dir / name).",
    ),
    branch: str | None = typer.Option(
        None, "--branch", "-b", help="Default branch for newly cloned repos."
    ),
    profile: ProfileOverrideOption = None,
) -> None:
    """Initialise a new workspace named `name`.

    Default location is `<workspace.workspaces_dir>/<name>` (the
    `workspaces_dir` setting defaults to `~/.untaped/workspaces`).
    """
    with report_errors(), profile_override(profile):
        target = path or (workspace_settings().workspaces_dir.expanduser() / name)
        bootstrapper = WorkspaceBootstrapper(ManifestRepository(), WorkspaceRegistryRepository())
        ws = InitWorkspace(bootstrapper)(target, name=name, branch=branch)
        typer.echo(f"initialised workspace {ws.name!r} at {ws.path}", err=True)


def adopt_command(
    path: Path = typer.Argument(..., help="Existing directory containing already-cloned repos."),
    name: str | None = typer.Option(None, "--name", "-n", help="Registry name (default: dirname)."),
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
            warn=lambda m: typer.echo(f"warning: {m}", err=True),
        )(path, name=name)
        ws = result.workspace
        n = len(result.repos)
        suffix = (
            " — nothing matched (use 'workspace add' to declare repos)"
            if result.discovered and n == 0
            else ""
        )
        typer.echo(
            f"adopted workspace {ws.name!r} at {ws.path} ({n} repo{'s' if n != 1 else ''}){suffix}",
            err=True,
        )


def forget_command(
    name: str = typer.Argument(..., help="Workspace name.", autocompletion=complete_workspace_name),
    prune: bool = typer.Option(
        False, "--prune", help="Also delete the workspace directory (refuses if dirty)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the prune confirmation prompt."),
    profile: ProfileOverrideOption = None,
) -> None:
    """Remove a workspace from the registry.

    The on-disk manifest and clones are preserved by default. Pass
    `--prune` to also remove the workspace directory (refused if any
    repo has uncommitted changes).
    """
    with report_errors(), profile_override(profile):
        if prune and not confirm(f"prune workspace directory for {name!r}?", yes=yes):
            typer.echo("aborted", err=True)
            raise typer.Exit(code=1)
        ws = ForgetWorkspace(
            WorkspaceRegistryRepository(),
            ManifestRepository(),
            fs=LocalFilesystem(),
            status=GitRunner(),
        )(name, prune=prune)
        action = "forgot and pruned" if prune else "forgot"
        typer.echo(f"{action} workspace {ws.name!r}", err=True)


def import_command(
    source: Path = typer.Argument(
        ..., help="Path to a YAML manifest (e.g. one cloned from a shared repo)."
    ),
    dest: Path = typer.Argument(..., help="Destination workspace directory."),
    name: str | None = typer.Option(None, "--name", "-n", help="Registry name override."),
    sync: bool = typer.Option(
        False,
        "--sync",
        help="Clone the imported repos immediately (only the repos in <source>).",
    ),
    profile: ProfileOverrideOption = None,
) -> None:
    """Adopt a workspace from a local YAML manifest."""
    with report_errors(), profile_override(profile):
        manifests = ManifestRepository()
        bootstrapper = WorkspaceBootstrapper(manifests, WorkspaceRegistryRepository())
        result = ImportWorkspace(manifests, bootstrapper)(source, path=dest, name=name)
        ws = result.workspace
        typer.echo(f"imported workspace {ws.name!r} at {ws.path}", err=True)
        if sync:
            outcomes = SyncWorkspace(
                ManifestRepository(),
                GitRunner(),
                fs=LocalFilesystem(),
                cache_dir=workspace_settings().cache_dir,
            )(ws, only=result.repos)
            print_sync_outcomes(outcomes, fmt="table", columns=None)
