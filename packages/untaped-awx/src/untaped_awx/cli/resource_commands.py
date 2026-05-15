"""Factory for per-resource Typer sub-apps."""

from __future__ import annotations

import queue
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from untaped_core import (
    ColumnsOption,
    OutputFormat,
    UntapedError,
    format_output,
    parse_kv_pairs,
    read_identifiers,
    report_errors,
)

from untaped_awx.application import (
    GetResource,
    ListResources,
    RunAction,
    SaveResource,
    StreamJobEvents,
    WatchJob,
)
from untaped_awx.application.apply_file import APPLY_PARALLEL_CAP
from untaped_awx.application.ports import JobMonitor, RawHttpResourceClient
from untaped_awx.cli._apply_runner import run_apply
from untaped_awx.cli._context import open_context, scope_for_spec
from untaped_awx.cli._event_render import render_event_text
from untaped_awx.cli._lookup import resolve_each
from untaped_awx.cli._names import flatten_fks
from untaped_awx.cli.membership_commands import register_membership_subapp
from untaped_awx.domain import Job, JobEvent
from untaped_awx.infrastructure.spec import AwxResourceSpec
from untaped_awx.infrastructure.yaml_io import dump_resource, write_resource


def make_resource_app(spec: AwxResourceSpec) -> typer.Typer:
    """Build the Typer sub-app for a single kind based on ``spec.commands``."""
    app = typer.Typer(
        name=spec.cli_name,
        help=f"Manage {spec.kind} resources.",
        no_args_is_help=True,
    )

    @app.callback()
    def _callback() -> None:
        """Sub-app dispatcher."""

    if "list" in spec.commands:
        _add_list(app, spec)
    if "get" in spec.commands:
        _add_get(app, spec)
    if "save" in spec.commands:
        _add_save(app, spec)
    if "apply" in spec.commands:
        _add_apply(app, spec)
    for action in spec.actions:
        builder = ACTION_BUILDERS.get(action.name)
        if builder is not None:
            builder(app, spec)
    for ref in spec.fk_refs:
        if ref.multi and ref.sub_endpoint:
            register_membership_subapp(app, spec, ref)

    return app


# ---- list ----


def _add_list(app: typer.Typer, spec: AwxResourceSpec) -> None:
    @app.command("list")
    def list_command(
        search: str | None = typer.Option(None, "--search", help="Fuzzy server-side search."),
        filter_: list[str] | None = typer.Option(
            None,
            "--filter",
            help=(
                "Server-side filter, KEY=VALUE (repeatable). Passed verbatim to "
                "AWX, so any Django-style lookup works: --filter "
                "organization__name=Default --filter name__icontains=deploy."
            ),
        ),
        limit: int | None = typer.Option(None, "--limit", help="Cap result count."),
        stdin: bool = typer.Option(
            False,
            "--stdin",
            help="Read names or numeric ids from stdin (one per line); render only those records.",
        ),
        with_names: bool = typer.Option(
            False,
            "--with-names",
            help=(
                "Replace FK ids with names from summary_fields. Multi-valued "
                "FKs (e.g. credentials) become lists of names."
            ),
        ),
        fmt: OutputFormat = typer.Option("table", "--format", "-f", help="Output format."),
        columns: list[str] | None = typer.Option(
            None, "--columns", "-c", help="Columns to include (repeatable)."
        ),
    ) -> None:
        """List resources, optionally restricted to names/ids from stdin.

        With ``--stdin``, reads newline-separated names or numeric ids and
        renders only those records — same identifier semantics as ``get
        --stdin`` but with the tabular columns view ``list`` uses. Cannot
        be combined with ``--search``/``--filter``/``--limit``.
        """
        if stdin and (search or filter_ or limit is not None):
            raise typer.BadParameter("--stdin cannot be combined with --search/--filter/--limit")
        records: list[dict[str, Any]] = []
        any_failed = False
        with report_errors(), open_context() as ctx:
            if stdin:
                ids = read_identifiers([], stdin=True)
                getter = GetResource(ctx.repo)
                records, any_failed = resolve_each(
                    ids, lambda n: getter.by_identifier(spec, n, scope=None)
                )
            else:
                filters = parse_kv_pairs(filter_, flag="--filter")
                records = list(
                    ListResources(ctx.repo)(spec, search=search, filters=filters, limit=limit)
                )
        cols = list(columns) if columns else list(spec.list_columns)
        if with_names:
            # Pass ``cols`` so display-only FK columns (e.g. Host's
            # ``inventory``, which lives in ``read_only_fields`` rather
            # than ``fk_refs``) get flattened from ``summary_fields``.
            records = flatten_fks(records, spec, columns=cols)
        # In ``--stdin`` mode every input identifier already reported its
        # own ``error:`` line; an all-failed batch leaves ``records``
        # empty and we skip the redundant ``[]`` to keep stdout clean for
        # piping. In normal mode an empty list still renders (``[]`` for
        # json/yaml, header-only table, blank for raw) so downstream
        # tools like ``jq`` always see a valid document.
        if records or not stdin:
            typer.echo(format_output(records, fmt=fmt, columns=cols))
        if any_failed:
            raise typer.Exit(code=1)


# ---- get ----


def _add_get(app: typer.Typer, spec: AwxResourceSpec) -> None:
    @app.command("get", no_args_is_help=True)
    def get_command(
        names: list[str] | None = typer.Argument(
            None, help=f"{spec.kind} name(s) or numeric id(s)."
        ),
        stdin: bool = typer.Option(
            False, "--stdin", help="Read names or numeric ids from stdin (one per line)."
        ),
        organization: str | None = typer.Option(
            None, "--organization", help="Scope to organization (ignored for numeric ids)."
        ),
        inventory: str | None = typer.Option(
            None,
            "--inventory",
            help=(
                "Scope to inventory (Host/Group only). Without this, name "
                "lookup is global and ambiguous if the same name exists "
                "across inventories."
            ),
        ),
        inventory_organization: str | None = typer.Option(
            None,
            "--inventory-organization",
            help="Disambiguate same-named inventories across orgs (Host/Group only).",
        ),
        by_name: bool = typer.Option(
            False,
            "--by-name",
            help="Force name lookup (escape hatch for resources whose name is all digits).",
        ),
        with_names: bool = typer.Option(
            False,
            "--with-names",
            help="Replace FK ids with names from summary_fields.",
        ),
        fmt: OutputFormat = typer.Option("yaml", "--format", "-f"),
        columns: list[str] | None = typer.Option(None, "--columns", "-c"),
    ) -> None:
        """Fetch one or more resources by name or numeric id.

        All-digit identifiers are looked up by id, everything else by
        name within the resolved organization scope. Pass ``--by-name``
        to force name lookup when a resource's name happens to be all
        digits.
        """
        records: list[Any] = []
        any_failed = False
        with report_errors(), open_context() as ctx:
            ids = read_identifiers(list(names or []), stdin=stdin)
            scope = _scope(
                ctx,
                organization,
                spec,
                inventory=inventory,
                inventory_organization=inventory_organization,
            )
            getter = GetResource(ctx.repo)
            records, any_failed = resolve_each(
                ids, lambda n: getter.by_identifier(spec, n, scope=scope, by_name=by_name)
            )
        if records:
            cols = list(columns) if columns else default_get_columns(fmt, spec.list_columns)
            if with_names:
                # ``cols`` may be ``None`` for non-table formats — that's
                # fine; ``flatten_fks`` then only flattens declared fk_refs.
                records = flatten_fks(records, spec, columns=cols)
            typer.echo(format_output(records, fmt=fmt, columns=cols))
        if any_failed:
            raise typer.Exit(code=1)


def default_get_columns(fmt: OutputFormat, default_cols: Sequence[str]) -> list[str] | None:
    """Default column projection for ``get`` commands.

    Table needs a projection — a full AWX record (50+ fields) renders as
    an unreadable wall. raw stays one-column-per-line so pipelines that
    do ``get --format raw | …`` keep their established shape; yaml/json
    keep the full record so users can inspect every field. Reused by
    ``unified-templates get`` so the polymorphic browser shares the
    same logic without duplicating it.
    """
    if fmt == "table":
        return list(default_cols)
    return None


# ---- save ----


def _add_save(app: typer.Typer, spec: AwxResourceSpec) -> None:
    @app.command("save", no_args_is_help=True)
    def save_command(
        name: str = typer.Argument(..., help=f"{spec.kind} name."),
        output: Path | None = typer.Option(
            None, "--out", "-o", help="Write to FILE; default is stdout."
        ),
        organization: str | None = typer.Option(
            None, "--organization", help="Scope to organization."
        ),
        inventory: str | None = typer.Option(
            None,
            "--inventory",
            help="Scope to inventory (Host/Group only).",
        ),
        inventory_organization: str | None = typer.Option(
            None,
            "--inventory-organization",
            help="Disambiguate same-named inventories across orgs (Host/Group only).",
        ),
    ) -> None:
        """Dump the resource as a portable YAML envelope."""
        with report_errors(), open_context() as ctx:
            scope = _scope(
                ctx,
                organization,
                spec,
                inventory=inventory,
                inventory_organization=inventory_organization,
            )
            resource = SaveResource(ctx.repo, ctx.fk)(spec, name=name, scope=scope)
        comment = spec.fidelity_note if spec.fidelity != "full" else None
        if comment:
            typer.echo(f"{spec.fidelity} save: {comment}", err=True)
        if output:
            write_resource(output, resource, header_comment=comment)
        else:
            typer.echo(dump_resource(resource, header_comment=comment))


# ---- apply (per-kind, single file) ----


def _add_apply(app: typer.Typer, spec: AwxResourceSpec) -> None:
    @app.command("apply", no_args_is_help=True)
    def apply_command(
        file: Path = typer.Option(..., "--file", help="YAML file to apply."),
        yes: bool = typer.Option(False, "--yes", help="Actually write (default is preview only)."),
        fail_fast: bool = typer.Option(False, "--fail-fast", help="Abort on first error."),
        parallel: int = typer.Option(
            1,
            "--parallel",
            "-j",
            help=(
                "Concurrent doc writes within this kind. Phase 2 (membership) "
                f"stays serial. Capped at {APPLY_PARALLEL_CAP} "
                "(matches the HTTP connection pool default)."
            ),
        ),
        fmt: OutputFormat = typer.Option("table", "--format", "-f", help="Output format."),
        columns: list[str] | None = typer.Option(None, "--columns", "-c"),
    ) -> None:
        """Apply a YAML file. Default is preview-only — pass ``--yes`` to write.

        Wrong-kind docs in the file are warned about and **never written** —
        this command is scoped to the kind of its parent sub-app.
        """
        with report_errors(), open_context() as ctx:
            run_apply(
                ctx,
                file,
                write=yes,
                fail_fast=fail_fast,
                fmt=fmt,
                columns=columns,
                kind_filter=spec.kind,
                cli_name=spec.cli_name,
                parallel=parallel,
            )


# ---- launch ----


def _drain_parallel_with_worker(
    jobs: list[tuple[str, Job]],
    worker_fn: Callable[[str, Job], Job],
    *,
    while_running: Callable[[], None] | None = None,
) -> tuple[list[Job], list[tuple[str, UntapedError]]]:
    """Run ``worker_fn(name, job)`` concurrently and collect outcomes in
    launch order.

    ``UntapedError`` raised by ``worker_fn`` is captured into
    ``errors``; any other ``Exception`` is wrapped at the worker
    boundary as ``UntapedError("<ClassName>: <message>")``.

    ``while_running``, if given, runs on the main thread between
    ``pool.submit`` and result-collection — the seam a caller needs to
    interleave foreground work with the still-pending pool, before
    ``future.result()`` would block. It runs inside the same ``with``
    block, so a raise still triggers ``shutdown(wait=True)``.
    """

    def _wrap(name: str, job: Job) -> Job:
        # Catch ``Exception`` (not ``BaseException``) so ``KeyboardInterrupt``
        # propagates to the main thread for the executor's ``shutdown(wait=True)``
        # to cancel pending work cleanly. Widening this clause swallows Ctrl-C.
        try:
            return worker_fn(name, job)
        except UntapedError:
            raise
        except Exception as exc:
            raise UntapedError(f"{type(exc).__name__}: {exc}") from exc

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [(name, pool.submit(_wrap, name, job)) for name, job in jobs]
        if while_running is not None:
            while_running()
        results: list[Job] = []
        errors: list[tuple[str, UntapedError]] = []
        for name, future in futures:
            try:
                results.append(future.result())
            except UntapedError as exc:
                errors.append((name, exc))
    return results, errors


def _drain_parallel(
    monitor: JobMonitor,
    jobs: list[tuple[str, Job]],
    console: Console,
) -> tuple[list[Job], list[tuple[str, UntapedError]]]:
    """Drain ``--track`` events from multiple jobs concurrently.

    Workers stream :class:`JobEvent`s onto a :class:`queue.Queue`; the
    main thread drains the queue and prints with the originating
    template name as a prefix so concurrent output stays
    disambiguable on a shared stderr. After every worker has signalled
    completion (sentinel ``(name, None)``), each future's final
    :class:`Job` (post ``monitor.fetch``) is collected in launch order
    by :func:`_drain_parallel_with_worker` so the caller's per-job
    error stderr rows + ``any_failed`` exit-code semantics stay stable.

    Note: ``Ctrl-C`` may take up to one polling interval to abort
    because workers don't cooperatively cancel — the executor's
    ``shutdown(wait=True)`` blocks until each polling loop next
    iterates and the job goes terminal.
    """
    q: queue.Queue[tuple[str, JobEvent | None]] = queue.Queue()

    def _worker(name: str, job: Job) -> Job:
        # Sentinel pushed in ``finally`` *before* ``monitor.fetch`` so
        # a slow or failing fetch never blocks the main thread's queue
        # drain.
        try:
            for ev in StreamJobEvents(monitor)(job, follow=True):
                q.put((name, ev))
        finally:
            q.put((name, None))
        return monitor.fetch(job)

    def _drain_queue() -> None:
        # Single-threaded printing: queue drain runs only here so a
        # multi-segment Rich Text never interleaves between workers.
        done = 0
        while done < len(jobs):
            name, ev = q.get()
            if ev is None:
                done += 1
                continue
            console.print(render_event_text(ev, prefix=name))

    return _drain_parallel_with_worker(jobs, _worker, while_running=_drain_queue)


def _wait_parallel(
    client: RawHttpResourceClient,
    jobs: list[tuple[str, Job]],
) -> tuple[list[Job], list[tuple[str, UntapedError]]]:
    """Block-wait on multiple jobs concurrently — no streaming.

    Mirrors :func:`_drain_parallel` for the ``--wait`` (no
    ``--track``) path: each worker calls ``WatchJob(client)(job)``
    until the job hits a terminal state and returns. The
    executor / collection / error-wrap scaffolding lives in
    :func:`_drain_parallel_with_worker`.
    """
    watch = WatchJob(client)
    return _drain_parallel_with_worker(jobs, lambda _name, job: watch(job))


def _echo_parallel_errors(errors: list[tuple[str, UntapedError]]) -> bool:
    """Echo per-job errors from a parallel-monitor helper and return
    ``True`` when any were recorded so the caller can flip its
    ``any_failed`` flag with ``|=``.
    """
    for failed_name, failure in errors:
        typer.echo(f"error: {failed_name}: {failure}", err=True)
    return bool(errors)


# C901: per-kind launch-flag wiring dispatches over ``ActionSpec.accepts`` —
# one branch per accepted narrowable flag. Splitting would shred the
# structural guarantee the launch parser provides; see
# packages/untaped-awx/AGENTS.md (Resource framework → _add_launch).
def _add_launch(app: typer.Typer, spec: AwxResourceSpec) -> None:  # noqa: C901
    accepts = next((a.accepts for a in spec.actions if a.name == "launch"), frozenset())

    # Hide each narrowable flag whose payload field isn't in this
    # kind's ``ActionSpec.accepts``. ``_LAUNCH_FLAG_TO_ACCEPT`` is the
    # single source of truth for the flag→field mapping (also consulted
    # by the runtime guard); a hidden flag still parses, the guard
    # catches misuse.
    hide_inventory = _LAUNCH_FLAG_TO_ACCEPT["--inventory"] not in accepts
    hide_credential = _LAUNCH_FLAG_TO_ACCEPT["--credential"] not in accepts
    hide_scm_branch = _LAUNCH_FLAG_TO_ACCEPT["--scm-branch"] not in accepts
    hide_job_tag = _LAUNCH_FLAG_TO_ACCEPT["--job-tag"] not in accepts
    hide_skip_tag = _LAUNCH_FLAG_TO_ACCEPT["--skip-tag"] not in accepts
    hide_verbosity = _LAUNCH_FLAG_TO_ACCEPT["--verbosity"] not in accepts
    hide_diff_mode = _LAUNCH_FLAG_TO_ACCEPT["--diff-mode"] not in accepts
    hide_job_type = _LAUNCH_FLAG_TO_ACCEPT["--job-type"] not in accepts

    # C901: launch dispatch is a 2x2 matrix — ``--track`` vs ``--wait``,
    # parallel (>=2 templates) vs sequential — plus per-id error capture
    # and the ``--track`` job-status exit-code propagation. Splitting
    # either axis would lose the stable-stderr ordering guarantee
    # ``_drain_parallel`` provides or duplicate the body.
    @app.command("launch", no_args_is_help=True)
    def launch_command(  # noqa: C901
        names: list[str] | None = typer.Argument(None, help=f"{spec.kind} name(s)."),
        stdin: bool = typer.Option(False, "--stdin", help="Read names from stdin (one per line)."),
        organization: str | None = typer.Option(
            None, "--organization", help="Scope to organization."
        ),
        extra_vars: list[str] | None = typer.Option(
            None, "--extra-vars", help="KEY=VAL override (repeatable)."
        ),
        limit: str | None = typer.Option(None, "--limit", help="Hosts pattern to limit to."),
        inventory: str | None = typer.Option(
            None,
            "--inventory",
            help="Override inventory by name (resolved to id).",
            hidden=hide_inventory,
        ),
        credential: list[str] | None = typer.Option(
            None,
            "--credential",
            help="Override credential by name (repeatable; resolved to ids).",
            hidden=hide_credential,
        ),
        scm_branch: str | None = typer.Option(
            None, "--scm-branch", help="SCM branch to run from.", hidden=hide_scm_branch
        ),
        job_tag: list[str] | None = typer.Option(
            None,
            "--job-tag",
            help="Run only tasks with these tags (repeatable).",
            hidden=hide_job_tag,
        ),
        skip_tag: list[str] | None = typer.Option(
            None,
            "--skip-tag",
            help="Skip tasks with these tags (repeatable).",
            hidden=hide_skip_tag,
        ),
        verbosity: int | None = typer.Option(
            None, "--verbosity", help="0-4 (passed verbatim).", hidden=hide_verbosity
        ),
        diff_mode: bool | None = typer.Option(
            None,
            "--diff-mode/--no-diff-mode",
            help="Override diff_mode for this run.",
            hidden=hide_diff_mode,
        ),
        job_type: str | None = typer.Option(
            None,
            "--job-type",
            help="Override job_type (e.g. run, check).",
            hidden=hide_job_type,
        ),
        wait: bool = typer.Option(False, "--wait", help="Block until terminal."),
        track: bool = typer.Option(
            False,
            "--track",
            "-t",
            help=(
                "Stream structured events to stderr while waiting; exit 1 "
                "if any tracked job ends in a non-successful terminal state."
            ),
        ),
        fmt: OutputFormat = typer.Option(
            "table", "--format", "-f", help="Output format (json|yaml|table|raw)."
        ),
        columns: ColumnsOption = None,
    ) -> None:
        """Launch one or more resources and (optionally) wait for each job."""
        _reject_unsupported_launch_flags(
            kind=spec.kind,
            accepts=accepts,
            inventory=inventory,
            credential=credential,
            scm_branch=scm_branch,
            job_tag=job_tag,
            skip_tag=skip_tag,
            verbosity=verbosity,
            diff_mode=diff_mode,
            job_type=job_type,
        )
        jobs: list[Job] = []
        any_failed = False
        # Stderr console for ``--track``: ANSI when stderr is a TTY,
        # plain text when redirected (CI logs, piped through ``tee``).
        track_console = Console(stderr=True, highlight=False)
        with report_errors(), open_context() as ctx:
            scope = _scope(ctx, organization, spec)
            payload = _build_launch_payload(
                accepts=accepts,
                extra_vars=extra_vars,
                limit=limit,
                inventory=inventory,
                credential=credential,
                scm_branch=scm_branch,
                job_tag=job_tag,
                skip_tag=skip_tag,
                verbosity=verbosity,
                diff_mode=diff_mode,
                job_type=job_type,
                fk=ctx.fk,
                org_scope=scope,
            )
            ids = read_identifiers(list(names or []), stdin=stdin)
            # Launch phase — every launch is one HTTP POST returning an
            # in-flight Job; sequential keeps the per-id try/except simple
            # and the order of stderr error lines stable.
            launched: list[tuple[str, Job]] = []
            for n in ids:
                try:
                    job = RunAction(ctx.repo)(
                        spec,
                        name=n,
                        action="launch",
                        scope=scope,
                        payload=payload,
                    )
                    launched.append((n, job))
                except UntapedError as exc:
                    typer.echo(f"error: {n}: {exc}", err=True)
                    any_failed = True
            # Monitor phase — drains each launched job to its terminal
            # state. Two or more ``--track`` jobs run concurrently
            # (wall-clock = max, not sum); single-template stays
            # sequential for stable tracebacks and zero thread overhead.
            # ``--track`` takes precedence over ``--wait`` when both
            # are set, matching the single-template ``if track / elif
            # wait`` chain below.
            if track and len(launched) >= 2:
                results, errors = _drain_parallel(ctx.monitor, launched, track_console)
                jobs.extend(results)
                any_failed |= _echo_parallel_errors(errors)
            elif wait and len(launched) >= 2:
                results, errors = _wait_parallel(ctx.repo, launched)
                jobs.extend(results)
                any_failed |= _echo_parallel_errors(errors)
            else:
                for n, job in launched:
                    try:
                        if track:
                            # Render each event to stderr as it lands,
                            # then let the monitor's terminal flip end
                            # the loop. ``track_console`` carries the
                            # TTY-aware colour styling so green-ok /
                            # red-failed pop in a real terminal but
                            # stay plain text when piped.
                            for ev in StreamJobEvents(ctx.monitor)(job, follow=True):
                                track_console.print(render_event_text(ev))
                            job = ctx.monitor.fetch(job)
                        elif wait:
                            job = WatchJob(ctx.repo)(job)
                        jobs.append(job)
                    except UntapedError as exc:
                        typer.echo(f"error: {n}: {exc}", err=True)
                        any_failed = True
        if jobs:
            typer.echo(format_output([j.model_dump() for j in jobs], fmt=fmt, columns=columns))
        if track and any(j.status != "successful" for j in jobs):
            # --track promises CI-friendly exit codes: anything other than a
            # clean ``successful`` (failed/error/canceled, or still-running
            # if the monitor returned without terminal — which it shouldn't,
            # but be defensive) propagates as exit 1.
            raise typer.Exit(code=1)
        if any_failed:
            raise typer.Exit(code=1)


# Map CLI flag names → the field name in `ActionSpec.accepts` they target.
# extra_vars and limit are accepted by every launch-capable kind today, so
# they're not in the rejection map; if a future kind drops them, add them.
_LAUNCH_FLAG_TO_ACCEPT: dict[str, str] = {
    "--inventory": "inventory",
    "--credential": "credentials",
    "--scm-branch": "scm_branch",
    "--job-tag": "job_tags",
    "--skip-tag": "skip_tags",
    "--verbosity": "verbosity",
    "--diff-mode": "diff_mode",
    "--job-type": "job_type",
}


def _reject_unsupported_launch_flags(
    *,
    kind: str,
    accepts: frozenset[str],
    inventory: str | None,
    credential: list[str] | None,
    scm_branch: str | None,
    job_tag: list[str] | None,
    skip_tag: list[str] | None,
    verbosity: int | None,
    diff_mode: bool | None,
    job_type: str | None,
) -> None:
    """Fail loudly when the user supplies a flag this kind doesn't accept.

    Avoids the "parser acknowledges, code silently ignores" footgun. The
    flags are wired uniformly across kinds (Typer signature is shared in
    ``_add_launch``) but a workflow template's ``launch.accepts`` is a
    strict subset of a job template's, and silently dropping a value the
    user typed deliberately would be worse than rejecting up front.
    """
    supplied: dict[str, object] = {
        "--inventory": inventory,
        "--credential": credential,
        "--scm-branch": scm_branch,
        "--job-tag": job_tag,
        "--skip-tag": skip_tag,
        "--verbosity": verbosity,
        "--diff-mode": diff_mode,
        "--job-type": job_type,
    }
    bad = sorted(
        flag
        for flag, value in supplied.items()
        if value is not None and value != [] and _LAUNCH_FLAG_TO_ACCEPT[flag] not in accepts
    )
    if bad:
        raise typer.BadParameter(
            f"{kind}.launch does not accept {', '.join(bad)} "
            f"(supported: {', '.join(sorted(accepts))})"
        )


def _build_launch_payload(
    *,
    accepts: frozenset[str],
    extra_vars: list[str] | None,
    limit: str | None,
    inventory: str | None,
    credential: list[str] | None,
    scm_branch: str | None,
    job_tag: list[str] | None,
    skip_tag: list[str] | None,
    verbosity: int | None,
    diff_mode: bool | None,
    job_type: str | None,
    fk: Any,
    org_scope: dict[str, str] | None,
) -> dict[str, Any]:
    """Translate the launch CLI flags into the payload AAP expects.

    Only fields listed in this kind's ``ActionSpec.accepts`` are
    forwarded; flags for fields not in ``accepts`` are silently
    ignored. FK flags (``--inventory``, ``--credential``) resolve
    names to ids using the per-process :class:`FkResolver`.
    """
    payload: dict[str, Any] = {}
    if extra_vars and "extra_vars" in accepts:
        payload["extra_vars"] = "\n".join(extra_vars)
    if limit and "limit" in accepts:
        payload["limit"] = limit
    if inventory and "inventory" in accepts:
        payload["inventory"] = fk.name_to_id("Inventory", inventory, scope=org_scope)
    if credential and "credentials" in accepts:
        payload["credentials"] = [
            fk.name_to_id("Credential", c, scope=org_scope) for c in credential
        ]
    if scm_branch and "scm_branch" in accepts:
        payload["scm_branch"] = scm_branch
    if job_tag and "job_tags" in accepts:
        payload["job_tags"] = ",".join(job_tag)
    if skip_tag and "skip_tags" in accepts:
        payload["skip_tags"] = ",".join(skip_tag)
    if verbosity is not None and "verbosity" in accepts:
        payload["verbosity"] = verbosity
    if diff_mode is not None and "diff_mode" in accepts:
        payload["diff_mode"] = diff_mode
    if job_type and "job_type" in accepts:
        payload["job_type"] = job_type
    return payload


# ---- update (Project SCM sync) ----


def _add_update(app: typer.Typer, spec: AwxResourceSpec) -> None:
    # Project's ``update`` declares ``accepts=frozenset()``; no
    # payload-bearing flags exist yet. When one is added, mirror the
    # ``Option(hidden=...)`` narrowing pattern from ``_add_launch``.
    @app.command("update", no_args_is_help=True)
    def update_command(
        name: str = typer.Argument(..., help=f"{spec.kind} name."),
        organization: str | None = typer.Option(
            None, "--organization", help="Scope to organization."
        ),
        wait: bool = typer.Option(False, "--wait", help="Block until terminal."),
        fmt: OutputFormat = typer.Option(
            "table", "--format", "-f", help="Output format (json|yaml|table|raw)."
        ),
        columns: ColumnsOption = None,
    ) -> None:
        """Trigger an SCM sync (Project)."""
        with report_errors(), open_context() as ctx:
            scope = _scope(ctx, organization, spec)
            job = RunAction(ctx.repo)(spec, name=name, action="update", scope=scope)
            if wait:
                job = WatchJob(ctx.repo)(job)
        typer.echo(format_output([job.model_dump()], fmt=fmt, columns=columns))


# ---- helpers ----


def _scope(
    ctx: Any,
    organization: str | None,
    spec: AwxResourceSpec,
    *,
    inventory: str | None = None,
    inventory_organization: str | None = None,
) -> dict[str, str] | None:
    return scope_for_spec(
        spec,
        organization,
        ctx.default_organization,
        inventory=inventory,
        inventory_organization=inventory_organization,
    )


# Maps an :class:`ActionSpec.name` to the builder that wires its CLI
# command. Adding a new custom action means: (1) declare its
# :class:`ActionSpec` on the per-kind spec, (2) implement an
# ``_add_<action>(app, spec)`` builder above, and (3) register it
# here. :func:`make_resource_app` itself stays untouched as new
# actions are added.
ACTION_BUILDERS: dict[str, Callable[[typer.Typer, AwxResourceSpec], None]] = {
    "launch": _add_launch,
    "update": _add_update,
}
