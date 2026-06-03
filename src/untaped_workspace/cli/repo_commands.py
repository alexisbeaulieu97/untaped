"""Repository mutation commands for the workspace CLI."""

from __future__ import annotations

from pathlib import Path

import typer
from untaped import (
    ProfileOverrideOption,
    profile_override,
    read_identifiers,
    report_errors,
    resolve_each,
)

from untaped_workspace.application import AddRepo, RemoveRepo, SyncWorkspace
from untaped_workspace.cli.common import confirm, resolve_workspace, workspace_settings
from untaped_workspace.cli.completions import complete_workspace_name
from untaped_workspace.cli.ops_commands import print_sync_outcomes
from untaped_workspace.infrastructure import GitRunner, LocalFilesystem, ManifestRepository


def register_repo_commands(app: typer.Typer) -> None:
    app.command("add", no_args_is_help=True)(add_command)
    app.command("remove", no_args_is_help=True)(remove_command)


def add_command(
    urls: list[str] | None = typer.Argument(None, help="Repo URL(s) to add."),
    stdin: bool = typer.Option(False, "--stdin", help="Read repo URLs from stdin (one per line)."),
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace name.",
        autocompletion=complete_workspace_name,
    ),
    path: Path | None = typer.Option(None, "--path", "-p", help="Workspace path."),
    branch: str | None = typer.Option(
        None,
        "--branch",
        "-b",
        help="Per-repo branch override (applies uniformly to every URL).",
    ),
    repo_name: str | None = typer.Option(
        None,
        "--repo-name",
        help="Local alias for the repo (applies uniformly to every URL).",
    ),
    sync: bool = typer.Option(
        False,
        "--sync",
        help="Clone the newly added repos immediately (only the ones this command actually added).",
    ),
    profile: ProfileOverrideOption = None,
) -> None:
    """Add one or more repos to a workspace's manifest.

    Multiple URLs may be passed as positional args or via ``--stdin``;
    ``--branch`` and ``--repo-name`` apply uniformly to every URL in
    the batch. ``--sync`` only clones URLs that actually landed.
    """
    add_repo = AddRepo(ManifestRepository())
    any_failed = False
    with report_errors(), profile_override(profile):
        idents = read_identifiers(list(urls or []), stdin=stdin)
        if repo_name is not None and len(idents) > 1:
            raise typer.BadParameter(
                "--repo-name applies to a single URL; drop --repo-name or pass URLs one at a time."
            )
        ws = resolve_workspace(workspace, path)

        def _add_one(url: str) -> str:
            repo = add_repo(ws, url=url, repo_name=repo_name, branch=branch)
            typer.echo(f"added {repo.name} to {ws.name!r}", err=True)
            return repo.name

        added, any_failed = resolve_each(idents, _add_one)
        if sync and added:
            outcomes = SyncWorkspace(
                ManifestRepository(),
                GitRunner(),
                fs=LocalFilesystem(),
                cache_dir=workspace_settings().cache_dir,
            )(ws, only=added)
            print_sync_outcomes(outcomes, fmt="table", columns=None)
    if any_failed:
        raise typer.Exit(code=1)


def remove_command(
    repos: list[str] | None = typer.Argument(None, help="Repo URL(s) or alias(es) to remove."),
    stdin: bool = typer.Option(
        False, "--stdin", help="Read repo identifiers from stdin (one per line)."
    ),
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace name.",
        autocompletion=complete_workspace_name,
    ),
    path: Path | None = typer.Option(None, "--path", "-p", help="Workspace path."),
    prune: bool = typer.Option(
        False, "--prune", help="Also delete the local clone (refuses if dirty)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the prune confirmation prompt."),
    profile: ProfileOverrideOption = None,
) -> None:
    """Remove one or more repos from a workspace's manifest."""
    with report_errors(), profile_override(profile):
        idents = read_identifiers(list(repos or []), stdin=stdin)
        ws = resolve_workspace(workspace, path)
        remove_repo = RemoveRepo(ManifestRepository(), fs=LocalFilesystem(), status=GitRunner())

        def _remove_one(ident: str) -> None:
            if prune and not confirm(f"prune local clone for {ident!r} in {ws.name!r}?", yes=yes):
                typer.echo("aborted", err=True)
                raise typer.Exit(code=1)
            removed = remove_repo(ws, ident=ident, prune=prune)
            typer.echo(f"removed {removed.name} from {ws.name!r}", err=True)

        _, any_failed = resolve_each(idents, _remove_one)
    if any_failed:
        raise typer.Exit(code=1)
