"""Sync, status, and foreach commands for the workspace CLI."""

from __future__ import annotations

from collections import Counter
from typing import Annotated

from cyclopts import App, Parameter
from untaped.api import (
    ColumnsOption,
    FormatOption,
    OutputFormat,
    clamp_parallel,
    echo,
    raise_usage,
    render_rows,
    report_errors,
)

from untaped_workspace.application import Foreach, RepoSyncEngine, SyncWorkspaces, WorkspaceStatus
from untaped_workspace.cli.common import (
    RepoSelectorOption,
    WorkspaceNameOption,
    WorkspacePathOption,
    parallel_cap,
    progress_ui,
    resolve_workspace,
    target_workspaces,
    workspace_settings,
)
from untaped_workspace.domain import SyncAction, SyncOutcome
from untaped_workspace.infrastructure import (
    DEFAULT_SLOW_TIMEOUT,
    DEFAULT_TIMEOUT,
    GitRunner,
    LocalFilesystem,
    ManifestRepository,
    shell_runner,
)


def register_operation_commands(app: App) -> None:
    app.command(sync_command, name="sync")
    app.command(status_command, name="status")
    app.command(foreach_command, name="foreach")


def sync_command(
    *,
    workspace: WorkspaceNameOption = None,
    path: WorkspacePathOption = None,
    repo: RepoSelectorOption = None,
    prune: Annotated[
        bool,
        Parameter(name="--prune", negative="", help="Remove local clones not in the manifest."),
    ] = False,
    timeout: Annotated[
        float | None,
        Parameter(
            name="--timeout",
            help=(
                f"Per-call timeout ceiling (seconds) for every git invocation "
                f"this sync makes (read-only ops AND network clone/fetch). "
                f"Defaults: {DEFAULT_TIMEOUT:g}s for read-only ops, "
                f"{DEFAULT_SLOW_TIMEOUT:g}s for clone/fetch. Pass a single value "
                f"to cap both."
            ),
        ),
    ] = None,
    all_workspaces: Annotated[
        bool,
        Parameter(name="--all", negative="", help="Sync every registered workspace."),
    ] = False,
    parallel: Annotated[
        int,
        Parameter(
            name=["--parallel", "-j"],
            help=(
                "Concurrent repo sync jobs. Per-repo outcomes are rows, "
                "not exceptions, so the pool drains to completion. Capped "
                "at a CPU-relative ceiling; values "
                "above are clamped with a stderr warning."
            ),
        ),
    ] = 1,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Reconcile workspace clones with the manifest."""
    if timeout is not None and timeout <= 0:
        raise_usage("--timeout must be positive")
    if parallel < 1:
        raise_usage("--parallel must be >= 1")
    workers = clamp_parallel(parallel, cap=parallel_cap(), policy="2 * os.cpu_count()")
    with report_errors():
        targets = target_workspaces(workspace, path, all_workspaces=all_workspaces)
        runner = (
            GitRunner(timeout=timeout, slow_timeout=timeout) if timeout is not None else GitRunner()
        )
        engine = RepoSyncEngine(
            runner,
            fs=LocalFilesystem(),
            cache_dir=workspace_settings().cache_dir,
        )
        if all_workspaces and repo:
            echo(
                "warning: --all --repo filters per-workspace; workspaces without "
                "matching repos will be skipped, not rejected.",
                err=True,
            )
        ui = progress_ui()
        with ui.progress("Syncing repos…") as p:
            sweep = SyncWorkspaces(ManifestRepository(), engine, notify=p.update)
            outcomes = sweep(
                targets,
                only=repo,
                prune=prune,
                strict_only=not all_workspaces,
                parallel=workers,
            )
        ui.message("info", _sync_summary(outcomes))
        print_sync_outcomes(outcomes, fmt=fmt, columns=columns)


def print_sync_outcomes(
    outcomes: list[SyncOutcome],
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    rows: list[dict[str, object]] = [o.model_dump() for o in outcomes]
    rendered = render_rows(
        rows,
        fmt=fmt,
        columns=columns,
        kind="workspace.sync-outcome",
        empty="Nothing to sync; clones already match the manifest.",
    )
    if rendered:
        echo(rendered)


def _sync_summary(outcomes: list[SyncOutcome]) -> str:
    total = len(outcomes)
    noun = "repo" if total == 1 else "repos"
    if total == 0:
        return "sync complete: 0 repos"
    counts = Counter(o.action for o in outcomes)
    action_labels: tuple[tuple[SyncAction, str], ...] = (
        ("clone", "cloned"),
        ("pull", "pulled"),
        ("up-to-date", "up to date"),
        ("skip", "skipped"),
        ("remove", "removed"),
        ("unmatched", "unmatched"),
    )
    parts = [f"{counts[action]} {label}" for action, label in action_labels if counts[action]]
    return f"sync complete: {total} {noun} ({', '.join(parts)})"


def status_command(
    *,
    workspace: WorkspaceNameOption = None,
    path: WorkspacePathOption = None,
    all_workspaces: Annotated[
        bool,
        Parameter(name="--all", negative="", help="Status across all workspaces."),
    ] = False,
    repo: RepoSelectorOption = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Per-repo `git status` snapshot."""
    with report_errors():
        targets = target_workspaces(workspace, path, all_workspaces=all_workspaces)
        use_case = WorkspaceStatus(ManifestRepository(), GitRunner(), fs=LocalFilesystem())
        rows: list[dict[str, object]] = []
        with progress_ui().progress("Gathering workspace status…"):
            for ws in targets:
                for entry in use_case(ws, only=repo):
                    rows.append(entry.model_dump())
        rendered = render_rows(
            rows,
            fmt=fmt,
            columns=columns,
            kind="workspace.status",
            empty="No cloned repos. Run `untaped workspace sync` to clone from the manifest.",
        )
        if rendered:
            echo(rendered)


def foreach_command(
    cmd: Annotated[str, Parameter(help='Shell command (e.g. "git pull --rebase").')],
    /,
    *,
    workspace: WorkspaceNameOption = None,
    path: WorkspacePathOption = None,
    parallel: Annotated[
        int,
        Parameter(
            name=["--parallel", "-j"],
            help=(
                "Concurrent workers. Capped at a CPU-relative ceiling; "
                "values above are clamped with a stderr warning. Values "
                "<= 0 run serially (1 worker). Fail-fast cancellation is "
                "best-effort: in-flight commands run to completion; only "
                "queued work stops."
            ),
        ),
    ] = 1,
    continue_on_error: Annotated[
        bool,
        Parameter(
            name="--continue-on-error",
            negative="",
            help="Don't stop after a non-zero exit.",
        ),
    ] = False,
    ignore_errors: Annotated[
        bool,
        Parameter(
            name="--ignore-errors",
            negative="",
            help=(
                "Treat per-repo failures as non-fatal. Implies --continue-on-error "
                "and exits 0 even when some repos failed. Wins on exit code if both "
                "flags are passed. Failed repos are still listed in the summary."
            ),
        ),
    ] = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
    repo: RepoSelectorOption = None,
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
    with report_errors():
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
            if not outcomes:
                echo("No repos matched. Check --repo or the workspace manifest.", err=True)
            for o in outcomes:
                for line in o.stdout.splitlines():
                    echo(f"[{o.repo}] {line}")
                for line in o.stderr.splitlines():
                    echo(f"[{o.repo}] {line}", err=True)
                if o.returncode != 0:
                    echo(f"[{o.repo}] exit {o.returncode}", err=True)
            if failed:
                echo(f"failed in: {', '.join(failed)}", err=True)
        else:
            rows = [o.model_dump() for o in outcomes]
            rendered = render_rows(rows, fmt=fmt, columns=columns, kind="workspace.foreach-outcome")
            if rendered:
                echo(rendered)
        if failed and not ignore_errors:
            raise SystemExit(1)
