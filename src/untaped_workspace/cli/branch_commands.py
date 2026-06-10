"""Branch metadata commands for the workspace CLI."""

from __future__ import annotations

from typing import Annotated

from cyclopts import Parameter
from untaped import (
    ColumnsOption,
    FormatOption,
    OutputFormat,
    ProfileOverrideOption,
    create_app,
    echo,
    profile_override,
    render_rows,
    report_errors,
)

from untaped_workspace.application import (
    ApplyWorkspaceBranch,
    SetWorkspaceBranch,
    UnsetWorkspaceBranch,
)
from untaped_workspace.cli.common import (
    RepoSelectorOption,
    WorkspaceNameOption,
    WorkspacePathOption,
    resolve_workspace,
)
from untaped_workspace.domain import BranchApplyOutcome
from untaped_workspace.infrastructure import GitRunner, LocalFilesystem, ManifestRepository

app = create_app(
    name="branch",
    help="Manage workspace branch metadata.",
)


@app.command(name="set")
def branch_set_command(
    branch: Annotated[str, Parameter(help="Branch name to record in the manifest.")],
    /,
    *,
    repo: Annotated[
        str | None,
        Parameter(
            name=["--repo", "-r"],
            help="Repo name or URL to set; omit for the workspace default.",
        ),
    ] = None,
    apply_checkout: Annotated[
        bool,
        Parameter(
            name="--apply",
            negative="",
            help="After writing the manifest, checkout matching existing clones to the new branch.",
        ),
    ] = False,
    workspace: WorkspaceNameOption = None,
    path: WorkspacePathOption = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
    profile: ProfileOverrideOption = None,
) -> None:
    """Set the default branch or a repo branch override in ``untaped.yml``."""
    with report_errors(), profile_override(profile):
        ws = resolve_workspace(workspace, path)
        change = SetWorkspaceBranch(ManifestRepository())(ws, branch=branch, repo=repo)
        if change.repo is None:
            echo(f"set default branch for {change.workspace!r} to {change.branch}", err=True)
        else:
            echo(
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


@app.command(name="unset")
def branch_unset_command(
    *,
    repo: Annotated[
        str | None,
        Parameter(
            name=["--repo", "-r"],
            help="Repo name or URL to unset; omit for the workspace default.",
        ),
    ] = None,
    workspace: WorkspaceNameOption = None,
    path: WorkspacePathOption = None,
    profile: ProfileOverrideOption = None,
) -> None:
    """Unset the default branch or a repo branch override in ``untaped.yml``."""
    with report_errors(), profile_override(profile):
        ws = resolve_workspace(workspace, path)
        change = UnsetWorkspaceBranch(ManifestRepository())(ws, repo=repo)
        if change.repo is None:
            echo(f"unset default branch for {change.workspace!r}", err=True)
            return
        echo(
            f"unset branch for repo {change.repo!r} in {change.workspace!r}",
            err=True,
        )


@app.command(name="apply")
def branch_apply_command(
    *,
    repo: RepoSelectorOption = None,
    workspace: WorkspaceNameOption = None,
    path: WorkspacePathOption = None,
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
    echo(render_rows(rows, fmt=fmt, columns=columns))
