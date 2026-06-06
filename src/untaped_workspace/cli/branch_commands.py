"""Branch metadata commands for the workspace CLI."""

from __future__ import annotations

from pathlib import Path

import typer
from untaped import (
    ColumnsOption,
    FormatOption,
    OutputFormat,
    ProfileOverrideOption,
    profile_override,
    report_errors,
)

from untaped_workspace.application import (
    ApplyWorkspaceBranch,
    SetWorkspaceBranch,
    UnsetWorkspaceBranch,
)
from untaped_workspace.cli.common import RepoSelectorOption, resolve_workspace
from untaped_workspace.cli.completions import complete_workspace_name
from untaped_workspace.cli.rendering import render_rows
from untaped_workspace.domain import BranchApplyOutcome
from untaped_workspace.infrastructure import GitRunner, LocalFilesystem, ManifestRepository

app = typer.Typer(
    name="branch",
    help="Manage workspace branch metadata.",
    no_args_is_help=True,
)


@app.callback()
def _branch_callback() -> None:
    """Manage workspace branch metadata."""


@app.command("set", no_args_is_help=True)
def branch_set_command(
    branch: str = typer.Argument(..., help="Branch name to record in the manifest."),
    repo: str | None = typer.Option(
        None,
        "--repo",
        "-r",
        help="Repo name or URL to set; omit for the workspace default.",
    ),
    apply_checkout: bool = typer.Option(
        False,
        "--apply",
        help="After writing the manifest, checkout matching existing clones to the new branch.",
    ),
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace name.",
        autocompletion=complete_workspace_name,
    ),
    path: Path | None = typer.Option(None, "--path", "-p", help="Workspace path."),
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
    profile: ProfileOverrideOption = None,
) -> None:
    """Set the default branch or a repo branch override in ``untaped.yml``."""
    with report_errors(), profile_override(profile):
        ws = resolve_workspace(workspace, path)
        change = SetWorkspaceBranch(ManifestRepository())(ws, branch=branch, repo=repo)
        if change.repo is None:
            typer.echo(f"set default branch for {change.workspace!r} to {change.branch}", err=True)
        else:
            typer.echo(
                f"set branch for repo {change.repo!r} in {change.workspace!r} to {change.branch}",
                err=True,
            )
        if apply_checkout:
            outcomes = ApplyWorkspaceBranch(
                ManifestRepository(),
                GitRunner(),
                fs=LocalFilesystem(),
            )(ws, repo=change.repo)
            print_branch_apply_outcomes(outcomes, fmt=fmt, columns=columns)


@app.command("unset")
def branch_unset_command(
    repo: str | None = typer.Option(
        None,
        "--repo",
        "-r",
        help="Repo name or URL to unset; omit for the workspace default.",
    ),
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace name.",
        autocompletion=complete_workspace_name,
    ),
    path: Path | None = typer.Option(None, "--path", "-p", help="Workspace path."),
    profile: ProfileOverrideOption = None,
) -> None:
    """Unset the default branch or a repo branch override in ``untaped.yml``."""
    with report_errors(), profile_override(profile):
        ws = resolve_workspace(workspace, path)
        change = UnsetWorkspaceBranch(ManifestRepository())(ws, repo=repo)
        if change.repo is None:
            typer.echo(f"unset default branch for {change.workspace!r}", err=True)
            return
        typer.echo(
            f"unset branch for repo {change.repo!r} in {change.workspace!r}",
            err=True,
        )


@app.command("apply")
def branch_apply_command(
    repo: RepoSelectorOption = None,
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace name.",
        autocompletion=complete_workspace_name,
    ),
    path: Path | None = typer.Option(None, "--path", "-p", help="Workspace path."),
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
    profile: ProfileOverrideOption = None,
) -> None:
    """Checkout existing repos to the branch declared in ``untaped.yml``."""
    with report_errors(), profile_override(profile):
        ws = resolve_workspace(workspace, path)
        outcomes = ApplyWorkspaceBranch(
            ManifestRepository(),
            GitRunner(),
            fs=LocalFilesystem(),
        )(ws, repo=repo)
        print_branch_apply_outcomes(outcomes, fmt=fmt, columns=columns)


def print_branch_apply_outcomes(
    outcomes: list[BranchApplyOutcome],
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    rows = [row.model_dump() for row in outcomes]
    typer.echo(render_rows(rows, fmt=fmt, columns=columns))
