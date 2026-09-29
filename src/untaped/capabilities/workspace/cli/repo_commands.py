"""``workspace repos`` commands: list, add, and remove a workspace's declared repos."""

from __future__ import annotations

from typing import Annotated

from cyclopts import Parameter

from untaped.capabilities.workspace.application import (
    AddRepo,
    RemoveRepo,
    ShowWorkspace,
    SyncWorkspace,
)
from untaped.capabilities.workspace.cli.common import (
    LeadingWorkspaceArg,
    WorkspaceArg,
    resolve_workspace,
    workspace_settings,
)
from untaped.capabilities.workspace.cli.ops_commands import (
    any_sync_failed,
    print_sync_outcomes,
)
from untaped.capabilities.workspace.domain import (
    RepoAddOutcome,
    RepoRemoveOutcome,
    WorkspaceSummaryRow,
)
from untaped.capabilities.workspace.errors import WorkspaceError
from untaped.capabilities.workspace.infrastructure import (
    GitRunner,
    LocalFilesystem,
    YamlManifestRepository,
)
from untaped.capability_api import (
    ColumnsOption,
    DryRunOption,
    FormatOption,
    StdinOption,
    UsageError,
    YesOption,
    batch_apply,
    create_app,
    emit,
    finish,
    q,
    read_identifiers,
    read_stdin_input,
    report_errors,
    resolve_each,
    ui_context,
)

ADD_STDIN_KINDS = frozenset(
    {"github.repo", "github.repo_hit", "github.sweep_repo", "workspace.repo"}
)
"""Pipe kinds ``add --stdin`` reads URLs from (``clone_url`` or ``url``)."""

REMOVE_STDIN_KINDS = frozenset({"workspace.repo", "workspace.sync_outcome"})
"""Pipe kinds ``remove --stdin`` reads repo names from (``repo``)."""


app = create_app(
    name="repos",
    help="List, add, and remove the repos declared in a workspace's manifest.",
)


@app.command(name="list")
def list_command(
    workspace: WorkspaceArg = None,
    /,
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List the repos declared in a workspace's manifest.

    An empty workspace emits one ``workspace.repo.summary`` row so its
    default branch and path stay visible.
    """
    with report_errors():
        ws = resolve_workspace(workspace)
        details = ShowWorkspace(YamlManifestRepository())(ws)
        kind = (
            "workspace.repo.summary"
            if any(isinstance(row, WorkspaceSummaryRow) for row in details)
            else "workspace.repo"
        )
        emit(details, fmt=fmt, columns=columns, kind=kind)


def _require_workspace_first(
    workspace: str | None, idents: list[str], *, stdin: bool, what: str
) -> None:
    """Positional repos need the workspace before them; only ``--stdin`` may omit it."""
    if not stdin and workspace is not None and not idents:
        raise UsageError(
            f"missing {what} (or --stdin); the first argument is the workspace "
            "(`.` is the workspace containing the current directory)"
        )


@app.command(name="add")
def add_command(
    workspace: LeadingWorkspaceArg = None,
    urls: Annotated[
        list[str] | None,
        Parameter(negative="", help="Repo URLs to add."),
    ] = None,
    /,
    *,
    stdin: Annotated[
        StdinOption,
        Parameter(
            help=(
                "Read repo URLs from stdin: one per line, or a --format pipe stream "
                "of github.repo, github.repo_hit, github.sweep_repo (clone_url or url) "
                "or workspace.repo (url) records."
            ),
        ),
    ] = False,
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
                "Clone the newly added repos immediately (only the ones this "
                "command actually added); prints the sync rows instead of the add rows."
            ),
        ),
    ] = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Add one or more repos to a workspace's manifest.

    Multiple URLs may follow the workspace or come via ``--stdin``;
    ``--branch`` and ``--repo-name`` apply uniformly to every URL in
    the batch. ``--sync`` only clones URLs that actually landed.
    """
    add_repo = AddRepo(YamlManifestRepository())
    any_failed = False
    with report_errors():
        _require_workspace_first(workspace, list(urls or []), stdin=stdin, what="URL")
        idents = _read_add_urls(list(urls or []), stdin=stdin)
        if repo_name is not None and len(idents) > 1:
            raise UsageError(
                "--repo-name applies to a single URL; drop --repo-name or pass URLs one at a time"
            )
        ws = resolve_workspace(workspace)
        ui = ui_context(strict=False)

        def _add_one(url: str) -> RepoAddOutcome:
            repo = add_repo(ws, url=url, repo_name=repo_name, branch=branch)
            ui.success(f"added {repo.name} to {q(ws.name)}")
            return RepoAddOutcome(
                workspace=ws.name,
                repo=repo.name,
                url=repo.url,
                branch=repo.branch,
                target_path=ws.path / repo.name,
            )

        added, any_failed = resolve_each(idents, _add_one)
        if sync and added:
            outcomes = SyncWorkspace(
                YamlManifestRepository(),
                GitRunner(),
                fs=LocalFilesystem(),
                cache_dir=workspace_settings().cache_dir,
            )(ws, only=[row.repo for row in added])
            print_sync_outcomes(outcomes, fmt=fmt, columns=columns)
            any_failed = any_failed or any_sync_failed(outcomes)
        elif added:
            emit(
                added,
                fmt=fmt,
                columns=columns,
                kind="workspace.add_outcome",
            )
    finish(any_failed)


def _read_add_urls(urls: list[str], *, stdin: bool) -> list[str]:
    """Positional URLs, or stdin lines / :data:`ADD_STDIN_KINDS` records."""
    if not stdin:
        return read_identifiers(urls, stdin=False)
    if urls:
        raise UsageError("provide identifiers as positional args or via --stdin, not both")
    piped = read_stdin_input(accept_kinds=ADD_STDIN_KINDS)
    if piped.records is None:
        return list(piped.values)
    found: list[str] = []
    for env in piped.records:
        value = env.record.get("clone_url") or env.record.get("url")
        if not isinstance(value, str) or not value.strip():
            raise WorkspaceError(
                f"line {env.lineno}: record has no 'clone_url' or 'url'", category="invalid"
            )
        found.append(value.strip())
    return found


@app.command(name="remove")
def remove_command(
    workspace: LeadingWorkspaceArg = None,
    repos: Annotated[
        list[str] | None,
        Parameter(negative="", help="Repo URLs or aliases to remove."),
    ] = None,
    /,
    *,
    stdin: Annotated[
        StdinOption,
        Parameter(
            help=(
                "Read repo identifiers from stdin: one per line, or a --format pipe "
                "stream of workspace.repo or workspace.sync_outcome records (repo)."
            ),
        ),
    ] = False,
    prune: Annotated[
        bool,
        Parameter(
            name="--prune",
            negative="",
            help="Also delete the local clone (refuses unsafe local state).",
        ),
    ] = False,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Remove one or more repos from a workspace's manifest."""
    with report_errors():
        _require_workspace_first(workspace, list(repos or []), stdin=stdin, what="REPO")
        idents = read_identifiers(
            list(repos or []),
            stdin=stdin,
            id_field="repo",
            accept_kinds=REMOVE_STDIN_KINDS,
        )
        ws = resolve_workspace(workspace)
        remove_repo = RemoveRepo(
            YamlManifestRepository(),
            fs=LocalFilesystem(),
            prune_safety=GitRunner(),
        )
        ui = ui_context(strict=False)

        def _remove_one(ident: str) -> RepoRemoveOutcome:
            removed = remove_repo(ws, ident=ident, prune=prune)
            ui.success(f"removed {removed.name} from {q(ws.name)}")
            return RepoRemoveOutcome(
                workspace=ws.name, repo=removed.name, action="removed", pruned=prune
            )

        outcome = batch_apply(
            idents,
            _remove_one,
            verb="remove",
            noun="repo",
            label=lambda ident: ident,
            describe=lambda ident: {"workspace": ws.name, "repo": ident},
            ui=ui,
            destructive=prune,
            assume_yes=yes,
            preview_only=dry_run,
        )
        if dry_run:
            rows = [
                RepoRemoveOutcome(workspace=ws.name, repo=ident, action="planned", pruned=prune)
                for ident in idents
            ]
        else:
            rows = [row for _, row in outcome.results]
        if rows:
            emit(
                rows,
                fmt=fmt,
                columns=columns,
                kind="workspace.remove_outcome",
            )
    finish(outcome)
