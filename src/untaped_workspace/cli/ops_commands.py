"""Sync, status, and foreach commands for the workspace CLI."""

from __future__ import annotations

from pathlib import Path

import typer
from untaped import (
    ColumnsOption,
    FormatOption,
    OutputFormat,
    ProfileOverrideOption,
    clamp_parallel,
    profile_override,
    report_errors,
)

from untaped_workspace.application import Foreach, SyncWorkspace, SyncWorkspaces, WorkspaceStatus
from untaped_workspace.cli.common import (
    RepoSelectorOption,
    parallel_cap,
    resolve_workspace,
    target_workspaces,
    workspace_settings,
)
from untaped_workspace.cli.completions import complete_workspace_name
from untaped_workspace.cli.rendering import render_rows
from untaped_workspace.domain import SyncOutcome
from untaped_workspace.infrastructure import (
    DEFAULT_SLOW_TIMEOUT,
    DEFAULT_TIMEOUT,
    GitRunner,
    LocalFilesystem,
    ManifestRepository,
    shell_runner,
)


def register_operation_commands(app: typer.Typer) -> None:
    app.command("sync")(sync_command)
    app.command("status")(status_command)
    app.command("foreach", no_args_is_help=True)(foreach_command)


def sync_command(
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace name.",
        autocompletion=complete_workspace_name,
    ),
    path: Path | None = typer.Option(None, "--path", "-p", help="Workspace path."),
    repo: RepoSelectorOption = None,
    prune: bool = typer.Option(False, "--prune", help="Remove local clones not in the manifest."),
    timeout: float | None = typer.Option(
        None,
        "--timeout",
        help=(
            f"Per-call timeout ceiling (seconds) for every git invocation "
            f"this sync makes (read-only ops AND network clone/fetch). "
            f"Defaults: {DEFAULT_TIMEOUT:g}s for read-only ops, "
            f"{DEFAULT_SLOW_TIMEOUT:g}s for clone/fetch. Pass a single value "
            f"to cap both."
        ),
    ),
    all_workspaces: bool = typer.Option(False, "--all", help="Sync every registered workspace."),
    parallel: int = typer.Option(
        1,
        "--parallel",
        "-j",
        help=(
            "Concurrent workspaces (only with --all). Per-workspace "
            "outcomes are rows, not exceptions, so the pool drains "
            "to completion. Capped at a CPU-relative ceiling; values "
            "above are clamped with a stderr warning."
        ),
    ),
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
    profile: ProfileOverrideOption = None,
) -> None:
    """Reconcile workspace clones with the manifest."""
    if timeout is not None and timeout <= 0:
        raise typer.BadParameter("--timeout must be positive")
    if parallel < 1:
        raise typer.BadParameter("--parallel must be >= 1")
    if parallel > 1 and not all_workspaces:
        raise typer.BadParameter("--parallel >1 requires --all")
    workers = clamp_parallel(parallel, cap=parallel_cap(), policy="2 * os.cpu_count()")
    with report_errors(), profile_override(profile):
        targets = target_workspaces(workspace, path, all_workspaces=all_workspaces)
        runner = (
            GitRunner(timeout=timeout, slow_timeout=timeout) if timeout is not None else GitRunner()
        )
        sync = SyncWorkspace(
            ManifestRepository(),
            runner,
            fs=LocalFilesystem(),
            cache_dir=workspace_settings().cache_dir,
        )
        if all_workspaces and repo:
            typer.echo(
                "warning: --all --repo filters per-workspace; workspaces without "
                "matching repos will be skipped, not rejected.",
                err=True,
            )
        sweep = SyncWorkspaces(sync, notify=lambda m: typer.echo(m, err=True))
        outcomes = sweep(
            targets,
            only=repo,
            prune=prune,
            strict_only=not all_workspaces,
            parallel=workers,
        )
        print_sync_outcomes(outcomes, fmt=fmt, columns=columns)


def print_sync_outcomes(
    outcomes: list[SyncOutcome],
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    rows: list[dict[str, object]] = [o.model_dump() for o in outcomes]
    typer.echo(render_rows(rows, fmt=fmt, columns=columns))


def status_command(
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace name.",
        autocompletion=complete_workspace_name,
    ),
    path: Path | None = typer.Option(None, "--path", "-p", help="Workspace path."),
    all_workspaces: bool = typer.Option(False, "--all", help="Status across all workspaces."),
    repo: RepoSelectorOption = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
    profile: ProfileOverrideOption = None,
) -> None:
    """Per-repo `git status` snapshot."""
    with report_errors(), profile_override(profile):
        targets = target_workspaces(workspace, path, all_workspaces=all_workspaces)
        use_case = WorkspaceStatus(ManifestRepository(), GitRunner(), fs=LocalFilesystem())
        rows: list[dict[str, object]] = []
        for ws in targets:
            for entry in use_case(ws, only=repo):
                rows.append(entry.model_dump())
        typer.echo(render_rows(rows, fmt=fmt, columns=columns))


def foreach_command(
    cmd: str = typer.Argument(..., help='Shell command (e.g. "git pull --rebase").'),
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace name.",
        autocompletion=complete_workspace_name,
    ),
    path: Path | None = typer.Option(None, "--path", "-p", help="Workspace path."),
    parallel: int = typer.Option(
        1,
        "--parallel",
        "-j",
        help=(
            "Concurrent workers. Capped at a CPU-relative ceiling; "
            "values above are clamped with a stderr warning. Values "
            "<= 0 run serially (1 worker). Fail-fast cancellation is "
            "best-effort: in-flight commands run to completion; only "
            "queued work stops."
        ),
    ),
    continue_on_error: bool = typer.Option(
        False, "--continue-on-error", help="Don't stop after a non-zero exit."
    ),
    ignore_errors: bool = typer.Option(
        False,
        "--ignore-errors",
        help=(
            "Treat per-repo failures as non-fatal. Implies --continue-on-error "
            "and exits 0 even when some repos failed. Wins on exit code if both "
            "flags are passed. Failed repos are still listed in the summary."
        ),
    ),
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
    repo: RepoSelectorOption = None,
    profile: ProfileOverrideOption = None,
) -> None:
    """Run a shell command in each repo of the workspace.

    The default ``--format table`` is human-friendly: when each repo
    finishes, its captured stdout / stderr is replayed line-by-line
    with a ``[<repo>]`` prefix. Output is buffered per repo (the
    underlying runner uses ``capture_output=True``), so users running
    chatty commands won't see anything until that repo's command
    exits. Pass ``--format json|yaml|raw`` to emit ``ForeachOutcome``
    rows after every repo finishes — suitable for piping into ``jq``
    / ``awk`` / another ``untaped`` command.
    """
    with report_errors(), profile_override(profile):
        ws = resolve_workspace(workspace, path)
        workers = clamp_parallel(max(parallel, 1), cap=parallel_cap(), policy="2 * os.cpu_count()")
        keep_going = continue_on_error or ignore_errors
        outcomes = Foreach(ManifestRepository(), runner=shell_runner, fs=LocalFilesystem())(
            ws,
            command=cmd,
            parallel=workers,
            continue_on_error=keep_going,
            only=repo,
        )
        failed = [o.repo for o in outcomes if o.returncode != 0]
        if fmt == "table":
            for o in outcomes:
                for line in o.stdout.splitlines():
                    typer.echo(f"[{o.repo}] {line}")
                for line in o.stderr.splitlines():
                    typer.echo(f"[{o.repo}] {line}", err=True)
                if o.returncode != 0:
                    typer.echo(f"[{o.repo}] exit {o.returncode}", err=True)
            if failed:
                typer.echo(f"failed in: {', '.join(failed)}", err=True)
        else:
            rows = [o.model_dump() for o in outcomes]
            typer.echo(render_rows(rows, fmt=fmt, columns=columns))
        if failed and not ignore_errors:
            raise typer.Exit(code=1)
