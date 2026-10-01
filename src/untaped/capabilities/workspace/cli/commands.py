"""Workspace command tree: ``create``, ``add``, ``list``, ``status``, ``path``, ``archive``."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import nullcontext
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter
from cyclopts.validators import Number

from untaped.capabilities.workspace.application.archive import ArchiveWorkspace
from untaped.capabilities.workspace.application.run import RunInRepos, RunTarget
from untaped.capabilities.workspace.cli.common import (
    BaseOption,
    BranchOption,
    NameArg,
    ReadOnlyOption,
    RepoOption,
    WorkspaceParallelOption,
    git_worktrees,
    locate,
    parallel_workers,
    provisioner,
    repo_args,
    run_argv,
    select_run_repos,
    status_reader,
    utc_now,
    workspace_dir,
    workspace_settings,
    workspaces_dir,
)
from untaped.capabilities.workspace.domain.models import ArchivedRecord, WorkspaceRecord
from untaped.capabilities.workspace.domain.records import (
    ArchiveOutcome,
    RepoOutcome,
    RunOutcome,
    StatusRow,
    WorkspaceRow,
)
from untaped.capabilities.workspace.domain.safety import archive_hint
from untaped.capabilities.workspace.errors import WorkspaceError
from untaped.capabilities.workspace.infrastructure import StateWorkspaceStore, SubprocessRunner
from untaped.capabilities.workspace.settings import WorkspaceSettings
from untaped.capability_api import (
    ColumnsOption,
    DryRunOption,
    FormatOption,
    OutputFormat,
    ParallelOption,
    StdinOption,
    UsageError,
    YesOption,
    create_app,
    echo,
    emit,
    finish,
    plural,
    q,
    report_error,
    report_errors,
    ui_context,
)

app = create_app(
    name="workspace",
    help=(
        "Create and archive task workspaces (git worktrees of several repos). "
        "Experimental: may change in a minor release."
    ),
)

REPO_OUTCOME = "workspace.repo_outcome"
ARCHIVE_OUTCOME = "workspace.archive_outcome"
RUN_OUTCOME = "workspace.run_outcome"


def create_command(
    name: Annotated[str, Parameter(help="Name of the workspace to create.")],
    /,
    *,
    repo: RepoOption = None,
    read_only: ReadOnlyOption = None,
    branch: BranchOption = None,
    base: BaseOption = None,
    stdin: StdinOption = False,
    parallel: WorkspaceParallelOption | None = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Create a workspace and check out its repos as git worktrees.

    In table format the workspace path is the last stdout line (the only one
    with -q), so `cd "$(untaped -q workspace create ...)"` works.
    """
    with report_errors():
        args = repo_args(repo, read_only, branch=branch, base=base, stdin=stdin)
        settings = workspace_settings()
        provision = provisioner(settings, parallel)
        with ui_context(strict=False).progress(f"Creating workspace {name}…"):
            rows = provision.create(name, args)
        _show_provisioned(rows, settings, name, fmt=fmt, columns=columns)


def add_command(
    name: NameArg = None,
    /,
    *,
    repo: RepoOption = None,
    read_only: ReadOnlyOption = None,
    branch: BranchOption = None,
    base: BaseOption = None,
    stdin: StdinOption = False,
    parallel: WorkspaceParallelOption | None = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Check out more repos into an existing workspace; present ones report unchanged."""
    with report_errors():
        args = repo_args(repo, read_only, branch=branch, base=base, stdin=stdin)
        settings = workspace_settings()
        record = locate(settings, name)
        provision = provisioner(settings, parallel)
        with ui_context(strict=False).progress(f"Adding repos to {record.name}…"):
            rows = provision.add(record, args)
        _show_provisioned(rows, settings, record.name, fmt=fmt, columns=columns)


def list_command(
    *,
    archived: Annotated[
        bool,
        Parameter(name="--archived", negative="", help="List archived workspaces instead."),
    ] = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List active workspaces (or archived ones)."""
    with report_errors():
        root = workspaces_dir(workspace_settings())
        store = StateWorkspaceStore()
        records: Sequence[WorkspaceRecord] = store.archived() if archived else store.active()
        rows = [_workspace_row(record, root) for record in records]
        emit(
            rows, fmt=fmt, columns=columns, kind="workspace.workspace", empty="No workspaces found."
        )


def status_command(
    name: NameArg = None,
    /,
    *,
    all_workspaces: Annotated[
        bool, Parameter(name="--all", negative="", help="Every active workspace.")
    ] = False,
    fetch: Annotated[
        bool,
        Parameter(name="--fetch", negative="", help="Fetch each repo first (status is offline)."),
    ] = False,
    check: Annotated[
        bool,
        Parameter(name="--check", negative="", help="Exit 3 when any repo would block `archive`."),
    ] = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Show each repo's branch, ahead/behind, local changes and archive blockers."""
    with report_errors():
        if all_workspaces and name is not None:
            raise UsageError("--all cannot be combined with a workspace name")
        settings = workspace_settings()
        records = StateWorkspaceStore().active() if all_workspaces else [locate(settings, name)]
        status = status_reader(settings, git_worktrees(settings))
        progress = ui_context(strict=False).progress("Fetching repos…") if fetch else nullcontext()
        with progress:
            rows = [row for record in records for row in status(record, fetch=fetch)]
        emit(rows, fmt=fmt, columns=columns, kind="workspace.status", empty="No repos found.")
        failures = [(row, row.error) for row in rows if row.error is not None]
        for row, error in failures:
            report_error(error, item=f"{row.workspace}/{row.dir}")
    finish(bool(failures), predicate_hit=check and any(row.blockers for row in rows))


def path_command(name: NameArg = None, /) -> None:
    """Print the workspace's absolute path."""
    with report_errors():
        settings = workspace_settings()
        echo(str(workspace_dir(settings, locate(settings, name).name)))


def archive_command(
    name: NameArg = None,
    /,
    *,
    force: Annotated[
        bool,
        Parameter(
            name="--force",
            negative="",
            help="Archive even when repos have unpushed or uncommitted work (confirms first).",
        ),
    ] = False,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Remove the workspace's worktrees and directory, and record it as archived.

    Refuses (exit 1, nothing removed) while any repo has uncommitted,
    stashed or unpushed work, unless --force. Local branches stay in the
    repo cache, so a later `create` with the same branch resumes the work.
    """
    with report_errors():
        settings = workspace_settings()
        located = locate(settings, name)
        git = git_worktrees(settings)
        root = workspaces_dir(settings)
        store = StateWorkspaceStore(workspaces_dir=root)
        archive = ArchiveWorkspace(store, git, workspaces_dir=root, now=utc_now)
        with archive.hold(located.name) as record:  # from the check through the removal
            rows = status_reader(settings, git)(record)
            blocked = [row for row in rows if row.blockers]
            if dry_run or (blocked and not force):
                _show_plan(rows, force=force, fmt=fmt, columns=columns)
                if dry_run:
                    return
                raise WorkspaceError(
                    f"{plural(len(blocked), 'repo')} would lose work; nothing archived",
                    hint=archive_hint(blocked),
                )
            if blocked:
                _confirm_discard(record.name, blocked, yes=yes)
            outcomes = archive(record, force=force)
        emit(outcomes, fmt=fmt, columns=columns, kind=ARCHIVE_OUTCOME)
        failed = any(row.failed for row in outcomes)
        if not failed:
            ui_context(strict=False).success(f"archived workspace {q(record.name)}")
    finish(failed)


def run_command(
    first: Annotated[
        str,
        Parameter(
            allow_leading_hyphen=True,
            help=(
                "Workspace name, or the command when it is the only argument "
                "(the workspace is then found from the current directory)."
            ),
        ),
    ],
    second: Annotated[
        str | None,
        Parameter(
            allow_leading_hyphen=True,
            help=(
                "The command: a shell string, a script file, or - to read a script "
                "from stdin. Runs in each repo directory with stdin from /dev/null."
            ),
        ),
    ] = None,
    /,
    *,
    repo: Annotated[
        list[str] | None,
        Parameter(
            name=["--repo", "-r"],
            negative="",
            consume_multiple=False,
            help="Only this repo, by display name or directory (repeatable).",
        ),
    ] = None,
    stdin: StdinOption = False,
    include_read_only: Annotated[
        bool,
        Parameter(name="--include-read-only", negative="", help="Also run in read-only repos."),
    ] = False,
    fail_fast: Annotated[
        bool,
        Parameter(
            name="--fail-fast",
            negative="",
            help="Stop starting repos after the first failure; the rest are skipped.",
        ),
    ] = False,
    timeout: Annotated[
        float,
        Parameter(
            name="--timeout",
            validator=Number(gt=0),
            help="Seconds allowed per repo before it is killed.",
        ),
    ] = 600.0,
    parallel: Annotated[
        ParallelOption,
        Parameter(
            help=(
                "Concurrent repos. Default: the workspace.parallel setting, else "
                "min(8, 2 x CPUs). Values above 2 x CPUs are clamped with a stderr warning."
            ),
        ),
    ]
    | None = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Run a command in each repo of a workspace.

    Each run gets UNTAPED_WORKSPACE, UNTAPED_REPO, UNTAPED_BRANCH, UNTAPED_BASE
    and UNTAPED_READ_ONLY in its environment. Exits 1 when any repo failed.
    """
    with report_errors():
        name, command = (None, first) if second is None else (first, second)
        if command == "-" and stdin:
            raise UsageError("a script on stdin (-) cannot be combined with --stdin")
        settings = workspace_settings()
        record = locate(settings, name)
        specs = select_run_repos(
            record.repos, repo=repo, stdin=stdin, include_read_only=include_read_only
        )
        root = workspace_dir(settings, record.name)
        targets = [RunTarget(record.name, spec, root / spec.dir) for spec in specs]
        human = fmt == "table"
        with run_argv(command) as argv:
            run = RunInRepos(
                SubprocessRunner(),
                parallel=parallel_workers(settings, parallel),
                timeout=timeout,
                fail_fast=fail_fast,
                on_done=_echo_block if human else None,
            )
            rows = run(targets, argv)
        if human:
            _show_run_summary(rows)
        else:
            emit(rows, fmt=fmt, columns=columns, kind=RUN_OUTCOME)
    finish(any(row.failed for row in rows))


def _echo_block(row: RunOutcome) -> None:
    """One finished repo's output: a header (with the reason unless it ran), stdout, stderr.

    Skipped rows print nothing (the summary counts them); stdout is flushed so
    blocks stream through pipes.
    """
    if row.action == "skipped":
        return
    reason = f" · {row.detail}" if row.action != "ran" and row.detail else ""
    echo(f"── {row.repo} ({row.dir}){reason} ──")
    for text in (row.stdout, row.stderr):
        if text:
            echo(text.rstrip("\n"))
    ui_context(strict=False).stdout.flush()


def _show_run_summary(rows: Sequence[RunOutcome]) -> None:
    failed = [row.dir for row in rows if row.failed]
    parts = [f"{sum(1 for row in rows if row.action == 'ran')} ok"]
    if failed:
        parts.append(f"{len(failed)} failed ({', '.join(failed)})")
    skipped = sum(1 for row in rows if row.action == "skipped")
    if skipped:
        parts.append(f"{skipped} skipped")
    ui_context(strict=False).message("error" if failed else "success", " · ".join(parts))


def _show_provisioned(
    rows: Sequence[RepoOutcome],
    settings: WorkspaceSettings,
    name: str,
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    """Emit ``create``/``add`` rows; in table format the workspace path follows on stdout.

    With ``-q`` (and nothing failed) the table is left out, so the path is the
    only stdout line. Other formats carry records only.
    """
    failed = any(row.failed for row in rows)
    ui = ui_context(strict=False)
    if fmt != "table":
        emit(rows, fmt=fmt, columns=columns, kind=REPO_OUTCOME)
    else:
        if failed or not ui.quiet:
            emit(rows, fmt=fmt, columns=columns, kind=REPO_OUTCOME)
        ready = sum(1 for row in rows if not row.failed)
        ui.success(f"workspace {q(name)}: {plural(ready, 'repo')} ready")
        echo(str(workspace_dir(settings, name)))
    finish(failed)


def _workspace_row(record: WorkspaceRecord, root: Path) -> WorkspaceRow:
    return WorkspaceRow(
        name=record.name,
        repos=len(record.repos),
        created_at=record.created_at,
        archived_at=record.archived_at if isinstance(record, ArchivedRecord) else None,
        target_path=root / record.name,
    )


def _show_plan(
    rows: Sequence[StatusRow], *, force: bool, fmt: OutputFormat, columns: list[str] | None
) -> None:
    """Emit what ``archive`` would do: ``skipped`` for blocked repos unless ``force``."""
    plan = [
        ArchiveOutcome(
            workspace=row.workspace,
            repo=row.repo,
            action="skipped" if row.blockers and not force else "planned",
            detail="; ".join(row.blockers),
            target_path=row.target_path,
        )
        for row in rows
    ]
    emit(plan, fmt=fmt, columns=columns, kind=ARCHIVE_OUTCOME)


def _confirm_discard(name: str, blocked: Sequence[StatusRow], *, yes: bool) -> None:
    details = "; ".join(f"{row.dir} ({', '.join(row.blockers)})" for row in blocked)
    ui_context(strict=False).confirm_or_cancel(
        f"Archive {name} and discard: {details}?",
        assume_yes=yes,
        refusal="archive --force requires --yes when not interactive",
    )


app.command(create_command, name="create")
app.command(add_command, name="add")
app.command(list_command, name="list")
app.command(status_command, name="status")
app.command(path_command, name="path")
app.command(archive_command, name="archive")
app.command(run_command, name="run")
