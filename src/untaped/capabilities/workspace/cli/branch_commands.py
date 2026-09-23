"""Branch metadata commands for the workspace CLI."""

from __future__ import annotations

from typing import Annotated

from cyclopts import Parameter

from untaped.capabilities.workspace.application import (
    ApplyWorkspaceBranch,
    SetWorkspaceBranch,
    UnsetWorkspaceBranch,
)
from untaped.capabilities.workspace.cli.common import (
    RepoSelectorOption,
    WorkspaceNameOption,
    WorkspacePathOption,
    progress_ui,
    resolve_workspace,
)
from untaped.capabilities.workspace.domain import BranchApplyOutcome
from untaped.capabilities.workspace.infrastructure import (
    GitRunner,
    LocalFilesystem,
    YamlManifestRepository,
)
from untaped.capability_api import (
    ColumnsOption,
    FormatOption,
    OutputFormat,
    create_app,
    emit,
    finish,
    q,
    report_errors,
    ui_context,
)

CreateOption = Annotated[
    bool,
    Parameter(
        name="--create",
        negative="",
        help=(
            "Create the target branch from the current clean HEAD when it exists "
            "neither locally nor on origin (otherwise such repos are skipped)."
        ),
    ),
]

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
    create: CreateOption = False,
    workspace: WorkspaceNameOption = None,
    path: WorkspacePathOption = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Set the default branch or a repo branch override in ``untaped.yml``."""
    with report_errors():
        ws = resolve_workspace(workspace, path)
        change = SetWorkspaceBranch(YamlManifestRepository())(ws, branch=branch, repo=repo)
        ui = ui_context(strict=False)
        if change.repo is None:
            ui.success(f"set default branch for {q(change.workspace)} to {change.branch}")
        else:
            ui.success(
                f"set branch for repo {q(change.repo)} in {q(change.workspace)} to {change.branch}"
            )
        if apply_checkout:
            with progress_ui().progress("Applying branches…"):
                outcomes = ApplyWorkspaceBranch(
                    YamlManifestRepository(),
                    GitRunner(),
                    fs=LocalFilesystem(),
                )(ws, repo=change.repo, create=create)
            print_branch_apply_outcomes(outcomes, fmt=fmt, columns=columns)
            finish(any(row.action == "failed" for row in outcomes))


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
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Unset the default branch or a repo branch override in ``untaped.yml``."""
    with report_errors():
        ws = resolve_workspace(workspace, path)
        change = UnsetWorkspaceBranch(YamlManifestRepository())(ws, repo=repo)
        ui = ui_context(strict=False)
        if change.repo is None:
            ui.success(f"unset default branch for {q(change.workspace)}")
        else:
            ui.success(f"unset branch for repo {q(change.repo)} in {q(change.workspace)}")
        emit(
            [change],
            fmt=fmt,
            columns=columns,
            kind="workspace.branch_unset_outcome",
        )


@app.command(name="apply")
def branch_apply_command(
    *,
    repo: RepoSelectorOption = None,
    create: CreateOption = False,
    workspace: WorkspaceNameOption = None,
    path: WorkspacePathOption = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Checkout existing repos to the branch declared in ``untaped.yml``."""
    with report_errors():
        ws = resolve_workspace(workspace, path)
        with progress_ui().progress("Applying branches…"):
            outcomes = ApplyWorkspaceBranch(
                YamlManifestRepository(),
                GitRunner(),
                fs=LocalFilesystem(),
            )(ws, repo=repo, create=create)
        print_branch_apply_outcomes(outcomes, fmt=fmt, columns=columns)
    finish(any(row.action == "failed" for row in outcomes))


def print_branch_apply_outcomes(
    outcomes: list[BranchApplyOutcome],
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    emit(
        outcomes,
        fmt=fmt,
        columns=columns,
        kind="workspace.branch_outcome",
        empty="No matching repos to checkout.",
    )
