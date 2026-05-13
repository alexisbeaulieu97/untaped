"""Typer commands for the workspace domain.

Thin layer: parses CLI arguments, delegates to application use cases,
formats output via :mod:`untaped_core.output`.
"""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import typer
from untaped_core import (
    ColumnsOption,
    FormatOption,
    OutputFormat,
    UntapedError,
    format_output,
    get_settings,
    read_identifiers,
    report_errors,
)

from untaped_workspace.application import (
    AddRepo,
    AdoptWorkspace,
    EditWorkspace,
    Foreach,
    ForgetWorkspace,
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
    DEFAULT_SLOW_TIMEOUT,
    DEFAULT_TIMEOUT,
    GitRunner,
    LocalFilesystem,
    LocalRepoDiscoverer,
    ManifestRepository,
    WorkspaceRegistryRepository,
    WorkspaceResolver,
    editor_runner,
    shell_runner,
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
) -> None:
    """Initialise a new workspace named `name`.

    Default location is `<workspace.workspaces_dir>/<name>` (the
    `workspaces_dir` setting defaults to `~/.untaped/workspaces`).
    """
    with report_errors():
        target = path or (get_settings().workspace.workspaces_dir.expanduser() / name)
        ws = InitWorkspace(
            ManifestRepository(), WorkspaceRegistryRepository(), fs=LocalFilesystem()
        )(target, name=name, branch=branch)
        typer.echo(f"initialised workspace {ws.name!r} at {ws.path}", err=True)


# adopt ----------------------------------------------------------------------


@app.command("adopt", no_args_is_help=True)
def adopt_command(
    path: Path = typer.Argument(..., help="Existing directory containing already-cloned repos."),
    name: str | None = typer.Option(None, "--name", "-n", help="Registry name (default: dirname)."),
) -> None:
    """Initialise a workspace from already-cloned repos under `path`.

    Each immediate subdirectory containing `.git` is recorded in the new
    `untaped.yml` with its current `origin` URL and checked-out branch.
    """
    with report_errors():
        result = AdoptWorkspace(
            ManifestRepository(),
            WorkspaceRegistryRepository(),
            LocalRepoDiscoverer(GitRunner()),
            fs=LocalFilesystem(),
            warn=lambda m: typer.echo(f"warning: {m}", err=True),
        )(path, name=name)
        ws = result.workspace
        n = len(result.repos)
        suffix = " — nothing matched (use 'workspace add' to declare repos)" if n == 0 else ""
        typer.echo(
            f"adopted workspace {ws.name!r} at {ws.path} ({n} repo{'s' if n != 1 else ''}){suffix}",
            err=True,
        )


# forget ---------------------------------------------------------------------


@app.command("forget", no_args_is_help=True)
def forget_command(
    name: str = typer.Argument(..., help="Workspace name.", autocompletion=complete_workspace_name),
    prune: bool = typer.Option(
        False, "--prune", help="Also delete the workspace directory (refuses if dirty)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the prune confirmation prompt."),
) -> None:
    """Remove a workspace from the registry.

    The on-disk manifest and clones are preserved by default. Pass
    `--prune` to also remove the workspace directory (refused if any
    repo has uncommitted changes).
    """
    with report_errors():
        if prune and not _confirm(f"prune workspace directory for {name!r}?", yes=yes):
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
            outcomes = SyncWorkspace(
                ManifestRepository(),
                GitRunner(),
                fs=LocalFilesystem(),
                cache_dir=get_settings().workspace.cache_dir,
            )(ws, only=[repo.name])
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
                removed = RemoveRepo(
                    ManifestRepository(), fs=LocalFilesystem(), status=GitRunner()
                )(ws, ident=ident, prune=prune)
                typer.echo(f"removed {removed.name} from {ws.name!r}", err=True)
            except UntapedError as exc:
                typer.echo(f"error: {ident}: {exc}", err=True)
                any_failed = True
    if any_failed:
        raise typer.Exit(code=1)


# sync -----------------------------------------------------------------------

# Upper bound on `sync --all --parallel`. Picked to match a generous
# laptop's network concurrency without overwhelming git's per-process
# locks; issue #30 will unify this with `foreach`'s clamp into a single
# policy.
_PARALLEL_CAP = 32


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
            f"to completion. Capped at {_PARALLEL_CAP} (issue #30 will "
            "unify the cap policy)."
        ),
    ),
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Reconcile workspace clones with the manifest."""
    if timeout is not None and timeout <= 0:
        raise typer.BadParameter("--timeout must be positive")
    if parallel < 1:
        raise typer.BadParameter("--parallel must be >= 1")
    if parallel > 1 and not all_workspaces:
        raise typer.BadParameter("--parallel >1 requires --all")
    workers = min(parallel, _PARALLEL_CAP)
    if parallel > workers:
        typer.echo(
            f"warning: --parallel {parallel} clamped to {workers} (issue #30 "
            "will unify the cap policy across sync and foreach)",
            err=True,
        )
    with report_errors():
        targets = _all_workspaces() if all_workspaces else [_resolve(name, path)]
        runner = (
            GitRunner(timeout=timeout, slow_timeout=timeout) if timeout is not None else GitRunner()
        )
        use_case = SyncWorkspace(
            ManifestRepository(),
            runner,
            fs=LocalFilesystem(),
            cache_dir=get_settings().workspace.cache_dir,
        )
        if all_workspaces and only:
            typer.echo(
                "warning: --all --only filters per-workspace; workspaces without "
                "matching repos will be skipped, not rejected.",
                err=True,
            )
        if all_workspaces and workers > 1 and len(targets) > 1:
            typer.echo(
                f"syncing {len(targets)} workspaces with up to {workers} workers",
                err=True,
            )
            outcomes = _sync_parallel(
                use_case,
                targets,
                only=only,
                prune=prune,
                strict_only=not all_workspaces,
                workers=workers,
            )
        else:
            outcomes = []
            for ws in targets:
                outcomes.extend(
                    use_case(ws, only=only, prune=prune, strict_only=not all_workspaces)
                )
        _print_sync_outcomes(outcomes, fmt=fmt, columns=columns)


def _sync_parallel(
    use_case: SyncWorkspace,
    targets: Sequence[Workspace],
    *,
    only: list[str] | None,
    prune: bool,
    strict_only: bool,
    workers: int,
) -> list[SyncOutcome]:
    """Dispatch ``use_case`` across ``targets`` on a ThreadPoolExecutor.

    Outcomes come back in ``as_completed`` order — sort them by input
    workspace order then repo so table/JSON output stays predictable,
    mirroring ``Foreach._run_parallel``'s tail sort. ``strict_only``
    matches ``SyncWorkspace.__call__``'s parameter so a future caller
    that opens this helper to single-workspace use can't silently lose
    strict ``--only`` semantics; the sweep drains to completion (no
    fail-fast) so a plain list of futures is enough.
    """
    outcomes: list[SyncOutcome] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(use_case, ws, only=only, prune=prune, strict_only=strict_only)
            for ws in targets
        ]
        for fut in as_completed(futures):
            outcomes.extend(fut.result())
    order = {ws.name: i for i, ws in enumerate(targets)}
    outcomes.sort(key=lambda o: (order.get(o.workspace, len(order)), o.repo))
    return outcomes


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
        use_case = WorkspaceStatus(ManifestRepository(), GitRunner(), fs=LocalFilesystem())
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
    parallel: int = typer.Option(
        1,
        "--parallel",
        "-j",
        help=(
            "Concurrent workers. Fail-fast cancellation is best-effort: "
            "in-flight commands run to completion; only queued work stops."
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
        ws = _resolve(name, path)
        keep_going = continue_on_error or ignore_errors
        outcomes = Foreach(ManifestRepository(), runner=shell_runner, fs=LocalFilesystem())(
            ws,
            command=cmd,
            parallel=parallel,
            continue_on_error=keep_going,
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
            typer.echo(format_output(rows, fmt=fmt, columns=columns))
        if failed and not ignore_errors:
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
        ws = ImportWorkspace(
            ManifestRepository(), WorkspaceRegistryRepository(), fs=LocalFilesystem()
        )(source, path=path, name=name)
        typer.echo(f"imported workspace {ws.name!r} at {ws.path}", err=True)
        if sync:
            outcomes = SyncWorkspace(
                ManifestRepository(),
                GitRunner(),
                fs=LocalFilesystem(),
                cache_dir=get_settings().workspace.cache_dir,
            )(ws)
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
        rc = EditWorkspace(WorkspaceRegistryRepository(), runner=editor_runner)(name, editor=editor)
        if rc != 0:
            raise typer.Exit(code=rc)
