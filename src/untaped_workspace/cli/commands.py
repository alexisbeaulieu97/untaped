"""Typer commands for the workspace domain.

Thin layer: parses CLI arguments, delegates to application use cases,
formats output via :mod:`untaped.output`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer
from untaped import (
    ColumnsOption,
    FormatOption,
    OutputFormat,
    ProfileOverrideOption,
    clamp_parallel,
    format_output,
    get_config_section,
    profile_override,
    read_identifiers,
    report_errors,
    resolve_each,
)

from untaped_workspace.application import (
    AddRepo,
    AdoptWorkspace,
    ApplyWorkspaceBranch,
    EditWorkspace,
    Foreach,
    ForgetWorkspace,
    ImportWorkspace,
    InitWorkspace,
    ListWorkspaces,
    RemoveRepo,
    SetWorkspaceBranch,
    ShellInit,
    ShowWorkspace,
    SyncWorkspace,
    SyncWorkspaces,
    UnsetWorkspaceBranch,
    WorkspaceBootstrapper,
    WorkspacePath,
    WorkspaceResolver,
    WorkspaceStatus,
)
from untaped_workspace.cli.completions import complete_workspace_name
from untaped_workspace.domain import BranchApplyOutcome, SyncOutcome, Workspace
from untaped_workspace.infrastructure import (
    DEFAULT_SLOW_TIMEOUT,
    DEFAULT_TIMEOUT,
    GitRunner,
    LocalFilesystem,
    LocalRepoDiscoverer,
    ManifestRepository,
    WorkspaceRegistryRepository,
    editor_runner,
    resolve_editor_argv,
    shell_runner,
)
from untaped_workspace.settings import WorkspaceSettings


def _workspace_settings() -> WorkspaceSettings:
    return get_config_section("workspace", WorkspaceSettings)


app = typer.Typer(
    name="workspace",
    help="Manage local git workspaces (collections of repos).",
    no_args_is_help=True,
)
branch_app = typer.Typer(
    name="branch",
    help="Manage workspace branch metadata.",
    no_args_is_help=True,
)

RepoSelectorOption = Annotated[
    list[str] | None,
    typer.Option(
        "--repo",
        "-r",
        help="Limit to these repos (repeatable; name or URL).",
    ),
]


@app.callback()
def _callback() -> None:
    """Manage local git workspaces."""


@branch_app.callback()
def _branch_callback() -> None:
    """Manage workspace branch metadata."""


app.add_typer(branch_app, name="branch")


# helpers --------------------------------------------------------------------


def _resolve(workspace: str | None, path: Path | None, *, cwd: Path | None = None) -> Workspace:
    if workspace is not None and path is not None:
        raise typer.BadParameter("--workspace and --path are mutually exclusive")
    return WorkspaceResolver(
        registry=WorkspaceRegistryRepository(),
        manifests=ManifestRepository(),
    ).resolve(name=workspace, path=path, cwd=cwd)


def _target_workspaces(
    workspace: str | None,
    path: Path | None,
    *,
    all_workspaces: bool,
) -> list[Workspace]:
    if all_workspaces:
        if workspace is not None or path is not None:
            raise typer.BadParameter("--all cannot be combined with --workspace or --path")
        return _all_workspaces()
    return [_resolve(workspace, path)]


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
    profile: ProfileOverrideOption = None,
) -> None:
    """List registered workspaces."""
    with report_errors(), profile_override(profile):
        use_case = ListWorkspaces(WorkspaceRegistryRepository())
        rows: list[dict[str, object]] = [_workspace_row(w) for w in use_case()]
        typer.echo(format_output(rows, fmt=fmt, columns=columns))


# show -----------------------------------------------------------------------


@app.command("show")
def show_command(
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
    """Show manifest details for one workspace."""
    with report_errors(), profile_override(profile):
        ws = _resolve(workspace, path)
        rows = [row.model_dump() for row in ShowWorkspace(ManifestRepository())(ws)]
        typer.echo(format_output(rows, fmt=fmt, columns=columns))


# branch ---------------------------------------------------------------------


@branch_app.command("set", no_args_is_help=True)
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
        ws = _resolve(workspace, path)
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
            _print_branch_apply_outcomes(outcomes, fmt=fmt, columns=columns)


@branch_app.command("unset")
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
        ws = _resolve(workspace, path)
        change = UnsetWorkspaceBranch(ManifestRepository())(ws, repo=repo)
        if change.repo is None:
            typer.echo(f"unset default branch for {change.workspace!r}", err=True)
            return
        typer.echo(
            f"unset branch for repo {change.repo!r} in {change.workspace!r}",
            err=True,
        )


@branch_app.command("apply")
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
        ws = _resolve(workspace, path)
        outcomes = ApplyWorkspaceBranch(
            ManifestRepository(),
            GitRunner(),
            fs=LocalFilesystem(),
        )(ws, repo=repo)
        _print_branch_apply_outcomes(outcomes, fmt=fmt, columns=columns)


def _print_branch_apply_outcomes(
    outcomes: list[BranchApplyOutcome],
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    rows = [row.model_dump() for row in outcomes]
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
    profile: ProfileOverrideOption = None,
) -> None:
    """Initialise a new workspace named `name`.

    Default location is `<workspace.workspaces_dir>/<name>` (the
    `workspaces_dir` setting defaults to `~/.untaped/workspaces`).
    """
    with report_errors(), profile_override(profile):
        target = path or (_workspace_settings().workspaces_dir.expanduser() / name)
        bootstrapper = WorkspaceBootstrapper(ManifestRepository(), WorkspaceRegistryRepository())
        ws = InitWorkspace(bootstrapper)(target, name=name, branch=branch)
        typer.echo(f"initialised workspace {ws.name!r} at {ws.path}", err=True)


# adopt ----------------------------------------------------------------------


@app.command("adopt", no_args_is_help=True)
def adopt_command(
    path: Path = typer.Argument(..., help="Existing directory containing already-cloned repos."),
    name: str | None = typer.Option(None, "--name", "-n", help="Registry name (default: dirname)."),
    profile: ProfileOverrideOption = None,
) -> None:
    """Adopt existing workspace state under `path`.

    If `path` already has `untaped.yml`, validate it and register the
    workspace without rewriting the manifest. Otherwise, each immediate
    subdirectory containing `.git` is recorded in a new manifest with
    its current `origin` URL and checked-out branch.
    """
    with report_errors(), profile_override(profile):
        bootstrapper = WorkspaceBootstrapper(ManifestRepository(), WorkspaceRegistryRepository())
        result = AdoptWorkspace(
            bootstrapper,
            LocalRepoDiscoverer(GitRunner()),
            fs=LocalFilesystem(),
            warn=lambda m: typer.echo(f"warning: {m}", err=True),
        )(path, name=name)
        ws = result.workspace
        n = len(result.repos)
        suffix = (
            " — nothing matched (use 'workspace add' to declare repos)"
            if result.discovered and n == 0
            else ""
        )
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
    profile: ProfileOverrideOption = None,
) -> None:
    """Remove a workspace from the registry.

    The on-disk manifest and clones are preserved by default. Pass
    `--prune` to also remove the workspace directory (refused if any
    repo has uncommitted changes).
    """
    with report_errors(), profile_override(profile):
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
    # Hoisted so post-``with`` exit dispatch is safe regardless of body outcome.
    any_failed = False
    with report_errors(), profile_override(profile):
        idents = read_identifiers(list(urls or []), stdin=stdin)
        # ``--repo-name`` is single-valued — refuse upfront when applying
        # it to multiple URLs would produce a guaranteed ``DuplicateRepoName``
        # cascade. Per-URL aliases require a structured input format
        # (out of scope; see issue #154).
        if repo_name is not None and len(idents) > 1:
            raise typer.BadParameter(
                "--repo-name applies to a single URL; drop --repo-name or pass URLs one at a time."
            )
        ws = _resolve(workspace, path)

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
                cache_dir=_workspace_settings().cache_dir,
            )(ws, only=added)
            _print_sync_outcomes(outcomes, fmt="table", columns=None)
    if any_failed:
        raise typer.Exit(code=1)


# remove ---------------------------------------------------------------------


@app.command("remove", no_args_is_help=True)
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
        ws = _resolve(workspace, path)
        remove_repo = RemoveRepo(ManifestRepository(), fs=LocalFilesystem(), status=GitRunner())

        def _remove_one(ident: str) -> None:
            if prune and not _confirm(f"prune local clone for {ident!r} in {ws.name!r}?", yes=yes):
                typer.echo("aborted", err=True)
                raise typer.Exit(code=1)
            removed = remove_repo(ws, ident=ident, prune=prune)
            typer.echo(f"removed {removed.name} from {ws.name!r}", err=True)

        _, any_failed = resolve_each(idents, _remove_one)
    if any_failed:
        raise typer.Exit(code=1)


# sync -----------------------------------------------------------------------


def _parallel_cap() -> int:
    """Cap value for ``sync --all`` and ``foreach`` parallelism.

    ``2 * os.cpu_count()`` matches the "I/O-bound work, threads cheap"
    rule of thumb both commands rely on (git fetch / shell exec are
    network- or syscall-bound, not CPU-bound). Computed per call so
    ``os.cpu_count`` monkeypatching in tests stays live.
    """
    return (os.cpu_count() or 1) * 2


@app.command("sync")
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
    workers = clamp_parallel(parallel, cap=_parallel_cap(), policy="2 * os.cpu_count()")
    with report_errors(), profile_override(profile):
        targets = _target_workspaces(workspace, path, all_workspaces=all_workspaces)
        runner = (
            GitRunner(timeout=timeout, slow_timeout=timeout) if timeout is not None else GitRunner()
        )
        sync = SyncWorkspace(
            ManifestRepository(),
            runner,
            fs=LocalFilesystem(),
            cache_dir=_workspace_settings().cache_dir,
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
        targets = _target_workspaces(workspace, path, all_workspaces=all_workspaces)
        use_case = WorkspaceStatus(ManifestRepository(), GitRunner(), fs=LocalFilesystem())
        rows: list[dict[str, object]] = []
        for ws in targets:
            for entry in use_case(ws, only=repo):
                rows.append(entry.model_dump())
        typer.echo(format_output(rows, fmt=fmt, columns=columns))


# foreach --------------------------------------------------------------------


@app.command("foreach", no_args_is_help=True)
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
        ws = _resolve(workspace, path)
        # Foreach silently coerces ``< 1`` to serial (issue spec) rather than
        # the BadParameter that ``sync`` and ``awx apply`` raise — different
        # commands, different UX calls on the lower bound.
        workers = clamp_parallel(max(parallel, 1), cap=_parallel_cap(), policy="2 * os.cpu_count()")
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
            typer.echo(format_output(rows, fmt=fmt, columns=columns))
        if failed and not ignore_errors:
            raise typer.Exit(code=1)


# import ---------------------------------------------------------------------


@app.command("import", no_args_is_help=True)
def import_command(
    source: Path = typer.Argument(
        ..., help="Path to a YAML manifest (e.g. one cloned from a shared repo)."
    ),
    dest: Path = typer.Argument(..., help="Destination workspace directory."),
    name: str | None = typer.Option(None, "--name", "-n", help="Registry name override."),
    sync: bool = typer.Option(
        False,
        "--sync",
        help="Clone the imported repos immediately (only the repos in <source>).",
    ),
    profile: ProfileOverrideOption = None,
) -> None:
    """Adopt a workspace from a local YAML manifest."""
    with report_errors(), profile_override(profile):
        manifests = ManifestRepository()
        bootstrapper = WorkspaceBootstrapper(manifests, WorkspaceRegistryRepository())
        result = ImportWorkspace(manifests, bootstrapper)(source, path=dest, name=name)
        ws = result.workspace
        typer.echo(f"imported workspace {ws.name!r} at {ws.path}", err=True)
        if sync:
            outcomes = SyncWorkspace(
                ManifestRepository(),
                GitRunner(),
                fs=LocalFilesystem(),
                cache_dir=_workspace_settings().cache_dir,
            )(ws, only=result.repos)
            _print_sync_outcomes(outcomes, fmt="table", columns=None)


# path -----------------------------------------------------------------------


@app.command("path", no_args_is_help=True)
def path_command(
    names: list[str] | None = typer.Argument(
        None, help="Workspace name(s).", autocompletion=complete_workspace_name
    ),
    stdin: bool = typer.Option(
        False, "--stdin", help="Read workspace names from stdin (one per line)."
    ),
    profile: ProfileOverrideOption = None,
) -> None:
    """Print the absolute path of one or more workspaces (one per line)."""
    get_path = WorkspacePath(WorkspaceRegistryRepository())
    # Hoisted so post-``with`` exit dispatch is safe regardless of body outcome.
    any_failed = False
    with report_errors(), profile_override(profile):
        idents = read_identifiers(list(names or []), stdin=stdin)

        def _echo_path(workspace_name: str) -> None:
            typer.echo(str(get_path(workspace_name)))

        _, any_failed = resolve_each(idents, _echo_path)
    if any_failed:
        raise typer.Exit(code=1)


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


@app.command("edit")
def edit_command(
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace name.",
        autocompletion=complete_workspace_name,
    ),
    path: Path | None = typer.Option(None, "--path", "-p", help="Workspace path."),
    editor: str | None = typer.Option(None, "--editor", "-e", help="Override $VISUAL/$EDITOR."),
    profile: ProfileOverrideOption = None,
) -> None:
    """Open the workspace directory in your editor."""
    with report_errors(), profile_override(profile):
        ws = _resolve(workspace, path)
        argv = resolve_editor_argv(editor)
        rc = EditWorkspace(runner=editor_runner)(ws, argv=argv)
        if rc != 0:
            raise typer.Exit(code=rc)


def _workspace_row(w: Workspace) -> dict[str, object]:
    # ``name`` first: under ``--format raw`` the first key is what
    # pipelines feed back into the next command (xargs identifier
    # semantics). See root AGENTS.md '--format raw
    # default-column contract'; pinned by tests/unit/test_format_raw_first_key.py.
    return {"name": w.name, "path": str(w.path)}
