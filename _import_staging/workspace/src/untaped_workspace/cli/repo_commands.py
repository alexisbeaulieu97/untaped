"""Repository mutation commands for the workspace CLI."""

from __future__ import annotations

from typing import Annotated

from cyclopts import App, Parameter
from untaped.api import (
    batch_apply,
    echo,
    finish,
    raise_usage,
    read_identifiers,
    report_errors,
    resolve_each,
    ui_context,
)

from untaped_workspace.application import AddRepo, RemoveRepo, SyncWorkspace
from untaped_workspace.cli.common import (
    WorkspaceNameOption,
    WorkspacePathOption,
    resolve_workspace,
    workspace_settings,
)
from untaped_workspace.cli.ops_commands import print_sync_outcomes
from untaped_workspace.infrastructure import GitRunner, LocalFilesystem, ManifestRepository


def register_repo_commands(app: App) -> None:
    app.command(add_command, name="add")
    app.command(remove_command, name="remove")


def add_command(
    urls: Annotated[list[str] | None, Parameter(help="Repo URL(s) to add.")] = None,
    *,
    stdin: Annotated[
        bool,
        Parameter(name="--stdin", negative="", help="Read repo URLs from stdin (one per line)."),
    ] = False,
    workspace: WorkspaceNameOption = None,
    path: WorkspacePathOption = None,
    branch: Annotated[
        str | None,
        Parameter(
            name=["--branch", "-b"],
            help="Per-repo branch override (applies uniformly to every URL).",
        ),
    ] = None,
    repo_name: Annotated[
        str | None,
        Parameter(
            name="--repo-name",
            help="Local alias for the repo (applies uniformly to every URL).",
        ),
    ] = None,
    sync: Annotated[
        bool,
        Parameter(
            name="--sync",
            negative="",
            help=(
                "Clone the newly added repos immediately "
                "(only the ones this command actually added)."
            ),
        ),
    ] = False,
) -> None:
    """Add one or more repos to a workspace's manifest.

    Multiple URLs may be passed as positional args or via ``--stdin``;
    ``--branch`` and ``--repo-name`` apply uniformly to every URL in
    the batch. ``--sync`` only clones URLs that actually landed.
    """
    add_repo = AddRepo(ManifestRepository())
    any_failed = False
    with report_errors():
        idents = read_identifiers(list(urls or []), stdin=stdin)
        if repo_name is not None and len(idents) > 1:
            raise_usage(
                "--repo-name applies to a single URL; drop --repo-name or pass URLs one at a time."
            )
        ws = resolve_workspace(workspace, path)

        def _add_one(url: str) -> str:
            repo = add_repo(ws, url=url, repo_name=repo_name, branch=branch)
            echo(f"added {repo.name} to {ws.name!r}", err=True)
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
    finish(any_failed)


def remove_command(
    repos: Annotated[
        list[str] | None,
        Parameter(help="Repo URL(s) or alias(es) to remove."),
    ] = None,
    *,
    stdin: Annotated[
        bool,
        Parameter(
            name="--stdin",
            negative="",
            help="Read repo identifiers from stdin (one per line).",
        ),
    ] = False,
    workspace: WorkspaceNameOption = None,
    path: WorkspacePathOption = None,
    prune: Annotated[
        bool,
        Parameter(
            name="--prune",
            negative="",
            help="Also delete the local clone (refuses unsafe local state).",
        ),
    ] = False,
    yes: Annotated[
        bool,
        Parameter(name=["--yes", "-y"], negative="", help="Skip the prune confirmation prompt."),
    ] = False,
) -> None:
    """Remove one or more repos from a workspace's manifest."""
    with report_errors():
        idents = read_identifiers(list(repos or []), stdin=stdin)
        ws = resolve_workspace(workspace, path)
        remove_repo = RemoveRepo(
            ManifestRepository(),
            fs=LocalFilesystem(),
            prune_safety=GitRunner(),
        )

        def _remove_one(ident: str) -> str:
            removed = remove_repo(ws, ident=ident, prune=prune)
            echo(f"removed {removed.name} from {ws.name!r}", err=True)
            return removed.name

        outcome = batch_apply(
            idents,
            _remove_one,
            verb="remove",
            noun="repo",
            label=lambda ident: ident,
            describe=lambda ident: {"workspace": ws.name, "repo": ident},
            ui=ui_context(strict=False),
            destructive=prune,
            assume_yes=yes,
        )
    finish(outcome)
