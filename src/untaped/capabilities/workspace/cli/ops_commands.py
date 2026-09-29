"""Sync, status, and foreach commands for the workspace CLI."""

from __future__ import annotations

import shlex
from collections import Counter
from functools import partial
from typing import Annotated

from cyclopts import App, Parameter

from untaped.capabilities.workspace.application import (
    Foreach,
    RepoSyncEngine,
    SyncWorkspaces,
    WorkspaceStatus,
)
from untaped.capabilities.workspace.cli.common import (
    LeadingWorkspaceArg,
    RepoSelectorOption,
    WorkspaceArg,
    WorkspaceParallelOption,
    leading_workspace,
    parallel_workers,
    target_workspaces,
    workspace_settings,
)
from untaped.capabilities.workspace.domain import (
    DEFAULT_FOREACH_TIMEOUT,
    ForeachOutcome,
    StatusEntry,
    SyncAction,
    SyncOutcome,
    Workspace,
)
from untaped.capabilities.workspace.errors import RegistryError
from untaped.capabilities.workspace.infrastructure import (
    DEFAULT_SLOW_TIMEOUT,
    DEFAULT_TIMEOUT,
    GitRunner,
    InterruptibleShellRunner,
    LocalFilesystem,
    YamlManifestRepository,
)
from untaped.capability_api import (
    ColumnsOption,
    ConfigError,
    DryRunOption,
    FormatOption,
    OperationCancelledError,
    OutputFormat,
    StdinOption,
    UsageError,
    YesOption,
    attribution,
    batch_apply,
    echo,
    emit,
    finish,
    hint,
    q,
    raise_usage,
    read_stdin_input,
    report_errors,
    summary,
    ui_context,
)

FOREACH_STDIN_KINDS = frozenset({"workspace.repo", "workspace.status", "workspace.sync_outcome"})
"""Pipe kinds ``foreach --stdin`` reads repo names from (``repo``)."""


def register_operation_commands(app: App) -> None:
    app.command(sync_command, name="sync")
    app.command(status_command, name="status")
    app.command(foreach_command, name="foreach")


def sync_command(
    workspace: WorkspaceArg = None,
    /,
    *,
    repo: RepoSelectorOption = None,
    prune: Annotated[
        bool,
        Parameter(
            name="--prune",
            negative="",
            help=(
                "Remove safe local clones not in the manifest; skips unsafe orphans. "
                "Confirms first unless --yes."
            ),
        ),
    ] = False,
    yes: YesOption = False,
    dry_run: Annotated[
        DryRunOption,
        Parameter(
            help=(
                "With --prune: skip the sync and list the orphan clones --prune "
                "would remove (planned) or keep (skipped); change nothing."
            ),
        ),
    ] = False,
    timeout: Annotated[
        float | None,
        Parameter(
            name="--timeout",
            help=(
                f"Per-call timeout ceiling (seconds) for every git invocation "
                f"this sync makes (local-only git ops AND network clone/fetch). "
                f"Defaults: {DEFAULT_TIMEOUT:g}s for local-only git ops, "
                f"{DEFAULT_SLOW_TIMEOUT:g}s for clone/fetch. Pass a single value "
                f"to cap both."
            ),
        ),
    ] = None,
    all_workspaces: Annotated[
        bool,
        Parameter(name="--all", negative="", help="Sync every registered workspace."),
    ] = False,
    parallel: WorkspaceParallelOption | None = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Reconcile workspace clones with the manifest.

    Per-repo outcomes are rows, not exceptions, so the worker pool drains to
    completion.
    """
    if timeout is not None and timeout <= 0:
        raise_usage("--timeout must be positive")
    if dry_run and not prune:
        raise_usage("--dry-run requires --prune")
    with report_errors():
        targets = target_workspaces(workspace, all_workspaces=all_workspaces)
        runner = (
            GitRunner(timeout=timeout, slow_timeout=timeout) if timeout is not None else GitRunner()
        )
        engine = RepoSyncEngine(
            runner,
            fs=LocalFilesystem(),
            cache_dir=workspace_settings().cache_dir,
        )
        # strict=False: a misconfigured ``ui.theme`` degrades the progress UI to
        # the default theme; feedback must never fail an otherwise-valid command.
        ui = ui_context(strict=False)
        if all_workspaces and repo:
            ui.message(
                "warning",
                "--all --repo filters per-workspace; workspaces without "
                "matching repos will be skipped, not rejected",
            )
        if dry_run:
            skipped, candidates = SyncWorkspaces(YamlManifestRepository(), engine).plan_prune(
                targets, skip_manifest_errors=all_workspaces, report_unavailable=True
            )
            planned = [
                SyncOutcome(
                    workspace=c.workspace.name,
                    repo=c.path.name,
                    target_path=c.path,
                    action="planned",
                    detail="no longer declared",
                )
                for c in candidates
            ]
            print_sync_outcomes([*skipped, *planned], fmt=fmt, columns=columns)
            return
        workers = parallel_workers(parallel)
        with ui.progress("Syncing repos…") as p:
            sweep = SyncWorkspaces(YamlManifestRepository(), engine, notify=p.update)
            outcomes = sweep(
                targets,
                only=repo,
                strict_only=not all_workspaces,
                skip_manifest_errors=all_workspaces,
                parallel=workers,
            )
        prune_failed = prune_cancelled = False
        if prune:
            # Prune after the sync phase (no racing in-flight clones) and
            # outside the spinner, behind the same batch confirmation as
            # `remove --prune` / `forget --prune`.
            skipped, candidates = sweep.plan_prune(targets, skip_manifest_errors=all_workspaces)
            outcomes.extend(skipped)
            try:
                pruned = batch_apply(
                    candidates,
                    engine.prune_candidate,
                    verb="prune",
                    noun="orphan clone",
                    label=lambda c: f"{c.workspace.name}/{c.path.name}",
                    describe=lambda c: {
                        "workspace": c.workspace.name,
                        "repo": c.path.name,
                        "path": str(c.path),
                    },
                    ui=ui,
                    destructive=True,
                    assume_yes=yes,
                )
            except ConfigError, UsageError:
                # A refused or interrupted prompt still reports the sync phase.
                ui.message("info", _sync_summary(outcomes))
                print_sync_outcomes(outcomes, fmt=fmt, columns=columns)
                raise
            outcomes.extend(row for _, row in pruned.results)
            prune_failed = pruned.any_failed
            prune_cancelled = pruned.cancelled
        ui.message("info", _sync_summary(outcomes))
        print_sync_outcomes(outcomes, fmt=fmt, columns=columns)
        if prune_cancelled:
            raise OperationCancelledError
    finish(any_sync_failed(outcomes) or prune_failed)


def print_sync_outcomes(
    outcomes: list[SyncOutcome],
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    emit(
        outcomes,
        fmt=fmt,
        columns=columns,
        kind="workspace.sync_outcome",
        empty="Nothing to sync; clones already match the manifest.",
    )


def any_sync_failed(outcomes: list[SyncOutcome]) -> bool:
    """Whether any sync row is a ``failed`` clone/fetch/status/pull."""
    return any(o.action == "failed" for o in outcomes)


def _sync_summary(outcomes: list[SyncOutcome]) -> str:
    counts = Counter(o.action for o in outcomes)
    actions: tuple[SyncAction, ...] = (
        "cloned",
        "pulled",
        "unchanged",
        "skipped",
        "failed",
        "removed",
        "unmatched",
        "unavailable",
    )
    return summary("sync", {action: counts[action] for action in actions})


def status_command(
    workspace: WorkspaceArg = None,
    /,
    *,
    all_workspaces: Annotated[
        bool,
        Parameter(name="--all", negative="", help="Status across all workspaces."),
    ] = False,
    repo: RepoSelectorOption = None,
    dirty: Annotated[
        bool,
        Parameter(
            name="--dirty",
            negative="",
            help="Only repos with uncommitted changes (modified or untracked files).",
        ),
    ] = False,
    behind: Annotated[
        bool,
        Parameter(
            name="--behind",
            negative="",
            help="Only repos behind their upstream. With --dirty, a repo matches either.",
        ),
    ] = False,
    check: Annotated[
        bool,
        Parameter(
            name="--check",
            negative="",
            help=(
                "Exit 3 when any repo is dirty or behind (only the --dirty / --behind "
                "condition when one is given)."
            ),
        ),
    ] = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Per-repo `git status` snapshot."""
    with report_errors():
        targets = target_workspaces(workspace, all_workspaces=all_workspaces)
        use_case = WorkspaceStatus(YamlManifestRepository(), GitRunner(), fs=LocalFilesystem())
        rows: list[StatusEntry] = []
        with ui_context(strict=False).progress("Gathering workspace status…"):
            for ws in targets:
                for entry in use_case(ws, only=repo, skip_manifest_errors=all_workspaces):
                    rows.append(entry)
        hits = [row for row in rows if row.needs_attention(dirty=dirty, behind=behind)]
        uninspected = [row for row in rows if not row.inspected]
        filtered = dirty or behind
        emit(
            # Filters never hide a repo whose state could not be read.
            [row for row in rows if row in hits or row in uninspected] if filtered else rows,
            fmt=fmt,
            columns=columns,
            kind="workspace.status",
            empty=(
                "No repos match the filters."
                if filtered
                else "No cloned repos. Run `untaped workspace sync` to clone from the manifest."
            ),
        )
    finish(check and bool(uninspected), predicate_hit=check and bool(hits))


def foreach_command(
    workspace: LeadingWorkspaceArg = None,
    cmd: Annotated[
        str | None,
        Parameter(name="CMD", help='Shell command (e.g. "git pull --rebase").'),
    ] = None,
    /,
    *,
    all_workspaces: Annotated[
        bool,
        Parameter(name="--all", negative="", help="Run in every registered workspace."),
    ] = False,
    stdin: Annotated[
        StdinOption,
        Parameter(
            help=(
                "Read the repos to run in from stdin: names, one per line, or a --format "
                "pipe stream of workspace.repo, workspace.status or workspace.sync_outcome "
                "records (repo). An empty pipe runs nothing."
            ),
        ),
    ] = False,
    parallel: WorkspaceParallelOption | None = None,
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
    timeout: Annotated[
        float,
        Parameter(
            name="--timeout",
            help=(
                "Per-repo command timeout in seconds. Commands that exceed "
                f"this return 124. Defaults to {DEFAULT_FOREACH_TIMEOUT:g}s."
            ),
        ),
    ] = DEFAULT_FOREACH_TIMEOUT,
) -> None:
    """Run a shell command in each repo of the workspace.

    Select repos with ``--repo`` or ``--stdin`` (not both); ``--all`` runs in
    every workspace, with ``--repo`` as a per-workspace filter.
    Fail-fast cancellation is best-effort under ``--parallel``: in-flight
    commands run to completion; only queued work stops.

    The default ``--format table`` is human-friendly: when each repo
    finishes, its captured stdout / stderr is replayed line-by-line
    with a ``[<repo>]`` prefix (in completion order under
    ``--parallel``). Output is buffered per repo (the
    underlying runner uses ``capture_output=True``), so users running
    chatty commands won't see anything until that repo's command
    exits. Pass ``--format json|yaml|raw`` to emit ``ForeachOutcome``
    rows after every repo finishes — suitable for piping into ``jq``
    / ``awk`` / another ``untaped`` command.
    """
    workspace, cmd = leading_workspace(workspace, cmd, missing="CMD")
    if timeout <= 0:
        raise_usage("--timeout must be positive")
    if stdin and (repo or all_workspaces):
        raise_usage("--stdin cannot be combined with --repo or --all")
    with report_errors():
        try:
            targets = target_workspaces(workspace, all_workspaces=all_workspaces)
        except RegistryError as exc:
            quoted = shlex.quote(f"{workspace} {cmd}")
            raise RegistryError(
                f"{exc}; quote a multi-word command to run it in the current workspace\n"
                + hint(f"workspace foreach {quoted}"),
                **attribution(exc),
            ) from exc
        only = _stdin_repos(targets[0]) if stdin else repo
        if stdin and not only:
            # An empty pipe selects nothing; say so rather than "No repos matched".
            emit(
                [],
                fmt=fmt,
                columns=columns,
                kind="workspace.foreach_outcome",
                empty="No repos received on stdin.",
            )
            return
        workers = parallel_workers(parallel)
        keep_going = continue_on_error or ignore_errors
        shell = InterruptibleShellRunner()
        ui = ui_context(strict=False)
        outcomes = Foreach(
            YamlManifestRepository(),
            runner=shell,
            fs=LocalFilesystem(),
            on_interrupt=shell.terminate_all,
            warn=lambda m: ui.message("warning", m),
        ).run_many(
            targets,
            command=cmd,
            parallel=workers,
            continue_on_error=keep_going,
            only=only,
            timeout=timeout,
            # Table output streams each repo's block as soon as it finishes.
            on_result=(
                partial(_echo_foreach_outcome, qualify=all_workspaces) if fmt == "table" else None
            ),
            strict_only=not all_workspaces,
            skip_manifest_errors=all_workspaces,
        )
        failed = [_label(o, qualify=all_workspaces) for o in outcomes if o.returncode != 0]
        if fmt == "table":
            if not outcomes:
                echo("No repos matched. Check --repo or the workspace manifest.", err=True)
            if failed:
                echo(f"failed in: {', '.join(failed)}", err=True)
        else:
            emit(outcomes, fmt=fmt, columns=columns, kind="workspace.foreach_outcome")
        finish(bool(failed) and not ignore_errors)


def _stdin_repos(ws: Workspace) -> list[str]:
    """Repo names piped to ``foreach --stdin``; records must belong to ``ws``."""
    piped = read_stdin_input(accept_kinds=FOREACH_STDIN_KINDS, allow_empty=True)
    if piped.records is None:
        return list(piped.values)
    names: list[str] = []
    for env in piped.records:
        source = env.record.get("workspace")
        if source is not None and source != ws.name:
            raise UsageError(
                f"line {env.lineno}: record is from workspace {q(str(source))}, "
                f"not {q(ws.name)}; pass {q(str(source))} as WS or filter the stream"
            )
        name = env.record.get("repo")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(
                f"line {env.lineno}: record 'repo' is missing or blank", category="invalid"
            )
        names.append(name.strip())
    return names


def _label(o: ForeachOutcome, *, qualify: bool) -> str:
    """``repo``, or ``workspace/repo`` under ``--all`` where names can repeat."""
    return f"{o.workspace}/{o.repo}" if qualify else o.repo


def _echo_foreach_outcome(o: ForeachOutcome, *, qualify: bool) -> None:
    label = _label(o, qualify=qualify)
    for line in o.stdout.splitlines():
        echo(f"[{label}] {line}")
    for line in o.stderr.splitlines():
        echo(f"[{label}] {line}", err=True)
    if o.returncode != 0:
        echo(f"[{label}] exit {o.returncode}", err=True)
