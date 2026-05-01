"""Typer commands for the workspace domain.

Thin layer: parses CLI arguments, delegates to application use cases,
formats output via :mod:`untaped_core.output`.
"""

from __future__ import annotations

from pathlib import Path

import typer
from untaped_core import (
    ColumnsOption,
    FormatOption,
    OutputFormat,
    UntapedError,
    format_output,
    read_identifiers,
    report_errors,
)

from untaped_workspace.application import (
    AddRepo,
    EditWorkspace,
    Foreach,
    ImportWorkspace,
    InitWorkspace,
    ListWorkspaces,
    RemoveRepo,
    ShellInit,
    SyncWorkspace,
    WorkspacePath,
    WorkspaceStatus,
)
from untaped_workspace.cli.completions import complete_workspace_name
from untaped_workspace.domain import SyncOutcome, Workspace
from untaped_workspace.infrastructure import (
    GitRunner,
    ManifestRepository,
    WorkspaceRegistryRepository,
    WorkspaceResolver,
)

app = typer.Typer(
    name="workspace",
    help="Manage local git workspaces (collections of repos).",
    no_args_is_help=True,
)


@app.callback()
def _callback() -> None:
    """Manage local git workspaces."""


# helpers --------------------------------------------------------------------


def _resolve(name: str | None, path: Path | None, *, cwd: Path | None = None) -> Workspace:
    return WorkspaceResolver().resolve(name=name, path=path, cwd=cwd)


def _all_workspaces() -> list[Workspace]:
    return WorkspaceRegistryRepository().entries()


def _confirm(prompt: str, *, yes: bool) -> bool:
    if yes:
        return True
    return typer.confirm(prompt, default=False)


# list -----------------------------------------------------------------------


@app.command("list")
def list_command(
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List registered workspaces."""
    with report_errors():
        use_case = ListWorkspaces(WorkspaceRegistryRepository())
        rows: list[dict[str, object]] = [{"name": w.name, "path": str(w.path)} for w in use_case()]
        typer.echo(format_output(rows, fmt=fmt, columns=columns))


# init -----------------------------------------------------------------------


@app.command("init", no_args_is_help=True)
def init_command(
    path: Path = typer.Argument(..., help="Workspace directory (created if missing)."),
    name: str | None = typer.Option(None, "--name", "-n", help="Registry name (default: dirname)."),
    branch: str | None = typer.Option(
        None, "--branch", "-b", help="Default branch for newly cloned repos."
    ),
) -> None:
    """Initialise a new workspace at `path`."""
    with report_errors():
        ws = InitWorkspace(ManifestRepository(), WorkspaceRegistryRepository())(
            path, name=name, branch=branch
        )
        typer.echo(f"initialised workspace {ws.name!r} at {ws.path}", err=True)


# add ------------------------------------------------------------------------


@app.command("add", no_args_is_help=True)
def add_command(
    url: str = typer.Argument(..., help="Repo URL to add to the workspace."),
    name: str | None = typer.Option(
        None,
        "--name",
        "-n",
        help="Workspace name.",
        autocompletion=complete_workspace_name,
    ),
    path: Path | None = typer.Option(None, "--path", "-p", help="Workspace path."),
    branch: str | None = typer.Option(None, "--branch", "-b", help="Per-repo branch override."),
    repo_name: str | None = typer.Option(None, "--repo-name", help="Local alias for the repo."),
    sync: bool = typer.Option(False, "--sync", help="Also clone the new repo immediately."),
) -> None:
    """Add a repo to a workspace's manifest."""
    with report_errors():
        ws = _resolve(name, path)
        repo = AddRepo(ManifestRepository())(ws, url=url, repo_name=repo_name, branch=branch)
        typer.echo(f"added {repo.name} to {ws.name!r}", err=True)
        if sync:
            outcomes = SyncWorkspace(ManifestRepository(), GitRunner())(ws, only=[repo.name])
            _print_sync_outcomes(outcomes, fmt="table", columns=None)


# remove ---------------------------------------------------------------------


@app.command("remove", no_args_is_help=True)
def remove_command(
    repos: list[str] | None = typer.Argument(None, help="Repo URL(s) or alias(es) to remove."),
    stdin: bool = typer.Option(
        False, "--stdin", help="Read repo identifiers from stdin (one per line)."
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        "-n",
        help="Workspace name.",
        autocompletion=complete_workspace_name,
    ),
    path: Path | None = typer.Option(None, "--path", "-p", help="Workspace path."),
    prune: bool = typer.Option(
        False, "--prune", help="Also delete the local clone (refuses if dirty)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the prune confirmation prompt."),
) -> None:
    """Remove one or more repos from a workspace's manifest."""
    any_failed = False
    with report_errors():
        idents = read_identifiers(list(repos or []), stdin=stdin)
        ws = _resolve(name, path)
        for ident in idents:
            if prune and not _confirm(f"prune local clone for {ident!r} in {ws.name!r}?", yes=yes):
                typer.echo("aborted", err=True)
                raise typer.Exit(code=1)
            try:
                removed = RemoveRepo(ManifestRepository(), status=GitRunner())(
                    ws, ident=ident, prune=prune
                )
                typer.echo(f"removed {removed.name} from {ws.name!r}", err=True)
            except UntapedError as exc:
                typer.echo(f"error: {ident}: {exc}", err=True)
                any_failed = True
    if any_failed:
        raise typer.Exit(code=1)


# sync -----------------------------------------------------------------------


@app.command("sync")
def sync_command(
    name: str | None = typer.Option(
        None,
        "--name",
        "-n",
        help="Workspace name.",
        autocompletion=complete_workspace_name,
    ),
    path: Path | None = typer.Option(None, "--path", "-p", help="Workspace path."),
    only: list[str] | None = typer.Option(
        None, "--only", help="Limit sync to these repos (repeatable)."
    ),
    prune: bool = typer.Option(False, "--prune", help="Remove local clones not in the manifest."),
    all_workspaces: bool = typer.Option(False, "--all", help="Sync every registered workspace."),
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Reconcile workspace clones with the manifest."""
    with report_errors():
        targets = _all_workspaces() if all_workspaces else [_resolve(name, path)]
        use_case = SyncWorkspace(ManifestRepository(), GitRunner())
        outcomes = []
        for ws in targets:
            outcomes.extend(use_case(ws, only=only, prune=prune))
        _print_sync_outcomes(outcomes, fmt=fmt, columns=columns)


def _print_sync_outcomes(
    outcomes: list[SyncOutcome],
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    rows: list[dict[str, object]] = [o.model_dump() for o in outcomes]
    typer.echo(format_output(rows, fmt=fmt, columns=columns))


# status ---------------------------------------------------------------------


@app.command("status")
def status_command(
    name: str | None = typer.Option(
        None,
        "--name",
        "-n",
        help="Workspace name.",
        autocompletion=complete_workspace_name,
    ),
    path: Path | None = typer.Option(None, "--path", "-p", help="Workspace path."),
    all_workspaces: bool = typer.Option(False, "--all", help="Status across all workspaces."),
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Per-repo `git status` snapshot."""
    with report_errors():
        targets = _all_workspaces() if all_workspaces else [_resolve(name, path)]
        use_case = WorkspaceStatus(ManifestRepository(), GitRunner())
        rows: list[dict[str, object]] = []
        for ws in targets:
            for entry in use_case(ws):
                rows.append(entry.model_dump())
        typer.echo(format_output(rows, fmt=fmt, columns=columns))


# foreach --------------------------------------------------------------------


@app.command("foreach", no_args_is_help=True)
def foreach_command(
    cmd: str = typer.Argument(..., help='Shell command (e.g. "git pull --rebase").'),
    name: str | None = typer.Option(
        None,
        "--name",
        "-n",
        help="Workspace name.",
        autocompletion=complete_workspace_name,
    ),
    path: Path | None = typer.Option(None, "--path", "-p", help="Workspace path."),
    parallel: int = typer.Option(1, "--parallel", "-j", help="Concurrent workers."),
    continue_on_error: bool = typer.Option(
        False, "--continue-on-error", help="Don't stop after a non-zero exit."
    ),
) -> None:
    """Run a shell command in each repo of the workspace."""
    with report_errors():
        ws = _resolve(name, path)
        outcomes = Foreach(ManifestRepository())(
            ws,
            command=cmd,
            parallel=parallel,
            continue_on_error=continue_on_error,
        )
        any_failed = False
        for o in outcomes:
            for line in o.stdout.splitlines():
                typer.echo(f"[{o.repo}] {line}")
            for line in o.stderr.splitlines():
                typer.echo(f"[{o.repo}] {line}", err=True)
            if o.returncode != 0:
                any_failed = True
                typer.echo(f"[{o.repo}] exit {o.returncode}", err=True)
        if any_failed:
            raise typer.Exit(code=1)


# import ---------------------------------------------------------------------


@app.command("import", no_args_is_help=True)
def import_command(
    source: Path = typer.Argument(
        ..., help="Path to a YAML manifest (e.g. one cloned from a shared repo)."
    ),
    path: Path = typer.Option(..., "--path", "-p", help="Destination workspace directory."),
    name: str | None = typer.Option(None, "--name", "-n", help="Registry name override."),
    sync: bool = typer.Option(False, "--sync", help="Clone repos after importing."),
) -> None:
    """Adopt a workspace from a local YAML manifest."""
    with report_errors():
        ws = ImportWorkspace(ManifestRepository(), WorkspaceRegistryRepository())(
            source, path=path, name=name
        )
        typer.echo(f"imported workspace {ws.name!r} at {ws.path}", err=True)
        if sync:
            outcomes = SyncWorkspace(ManifestRepository(), GitRunner())(ws)
            _print_sync_outcomes(outcomes, fmt="table", columns=None)


# path -----------------------------------------------------------------------


@app.command("path", no_args_is_help=True)
def path_command(
    name: str = typer.Argument(..., help="Workspace name.", autocompletion=complete_workspace_name),
) -> None:
    """Print the absolute path of a workspace (single line)."""
    with report_errors():
        p = WorkspacePath(WorkspaceRegistryRepository())(name)
        typer.echo(str(p))


# shell-init -----------------------------------------------------------------


@app.command("shell-init", no_args_is_help=True)
def shell_init_command(
    shell: str = typer.Argument(..., help='One of "zsh", "bash", "fish".'),
) -> None:
    """Emit a shell snippet defining `uwcd <workspace>`."""
    with report_errors():
        snippet = ShellInit()(shell)
        typer.echo(snippet, nl=False)


# edit -----------------------------------------------------------------------


@app.command("edit", no_args_is_help=True)
def edit_command(
    name: str = typer.Argument(..., help="Workspace name.", autocompletion=complete_workspace_name),
    editor: str | None = typer.Option(None, "--editor", "-e", help="Override $VISUAL/$EDITOR."),
) -> None:
    """Open the workspace directory in your editor."""
    with report_errors():
        rc = EditWorkspace(WorkspaceRegistryRepository())(name, editor=editor)
        if rc != 0:
            raise typer.Exit(code=rc)
