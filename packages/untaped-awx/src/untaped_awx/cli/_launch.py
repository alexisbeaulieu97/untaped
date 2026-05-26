"""``launch`` builder for the spec-driven CLI factory.

Owns the ``LAUNCH_FLAGS`` dispatch table (single source of truth for
the per-flag visibility / rejection / payload-translation triple), the
launch command body, and the per-job-error echo helper.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import typer
from rich.console import Console
from untaped_core import (
    ColumnsOption,
    FormatOption,
    UntapedError,
    format_output,
    read_identifiers,
    report_errors,
)

from untaped_awx.application import RunAction, StreamJobEvents, WatchJob
from untaped_awx.application.ports import FkResolver
from untaped_awx.cli._context import open_context, scope_for_command
from untaped_awx.cli._event_render import render_event_text
from untaped_awx.cli._parallel import _drain_parallel, _wait_parallel
from untaped_awx.domain import Job
from untaped_awx.infrastructure.spec import AwxResourceSpec


# C901: ``_add_launch`` defines a Typer command with 12 parameters, each
# carrying a ``hidden=hidden_by_flag[...]`` lookup from the per-kind
# ``ActionSpec.accepts`` projection. The complexity comes from the
# breadth of the Typer signature (one Option per launch flag), not from
# branchy dispatch — the eight per-flag branches now live in
# ``LAUNCH_FLAGS`` and are walked uniformly. Splitting the signature
# would mean either (a) parsing in a sibling function and rebinding —
# which Typer can't do — or (b) folding flags into a single ``--opt
# k=v`` glob — which would lose ``--help`` discoverability and
# per-flag typing.
def _add_launch(app: typer.Typer, spec: AwxResourceSpec) -> None:  # noqa: C901
    accepts = next((a.accepts for a in spec.actions if a.name == "launch"), frozenset())

    # Hide flags whose payload field isn't in this kind's
    # ``ActionSpec.accepts``. ``LAUNCH_FLAGS`` is the single source of
    # truth for the flag→field mapping (also consulted by the runtime
    # guard); a hidden flag still parses, the guard catches misuse.
    hidden_by_flag = {f.flag: f.accepts_key not in accepts for f in LAUNCH_FLAGS}

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
            hidden=hidden_by_flag["--inventory"],
        ),
        credential: list[str] | None = typer.Option(
            None,
            "--credential",
            help="Override credential by name (repeatable; resolved to ids).",
            hidden=hidden_by_flag["--credential"],
        ),
        scm_branch: str | None = typer.Option(
            None,
            "--scm-branch",
            help="SCM branch to run from.",
            hidden=hidden_by_flag["--scm-branch"],
        ),
        job_tag: list[str] | None = typer.Option(
            None,
            "--job-tag",
            help="Run only tasks with these tags (repeatable).",
            hidden=hidden_by_flag["--job-tag"],
        ),
        skip_tag: list[str] | None = typer.Option(
            None,
            "--skip-tag",
            help="Skip tasks with these tags (repeatable).",
            hidden=hidden_by_flag["--skip-tag"],
        ),
        verbosity: int | None = typer.Option(
            None, "--verbosity", help="0-4 (passed verbatim).", hidden=hidden_by_flag["--verbosity"]
        ),
        diff_mode: bool | None = typer.Option(
            None,
            "--diff-mode/--no-diff-mode",
            help="Override diff_mode for this run.",
            hidden=hidden_by_flag["--diff-mode"],
        ),
        job_type: str | None = typer.Option(
            None,
            "--job-type",
            help="Override job_type (e.g. run, check).",
            hidden=hidden_by_flag["--job-type"],
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
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Launch one or more resources and (optionally) wait for each job."""
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
        _reject_unsupported_launch_flags(kind=spec.kind, accepts=accepts, supplied=supplied)
        jobs: list[Job] = []
        any_failed = False
        # Stderr console for ``--track``: ANSI when stderr is a TTY,
        # plain text when redirected (CI logs, piped through ``tee``).
        track_console = Console(stderr=True, highlight=False)
        with report_errors(), open_context() as ctx:
            scope = scope_for_command(ctx, organization, spec)
            payload = _build_launch_payload(
                accepts=accepts,
                extra_vars=extra_vars,
                limit=limit,
                supplied=supplied,
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


@dataclass(frozen=True)
class LaunchFlag:
    """One row of the launch-flag dispatch table.

    Each launch CLI flag has three orthogonal concerns that used to be
    walked separately: visibility (``--help`` hides it on kinds that
    don't accept the field), validation (rejecting the flag on those
    kinds at runtime), and translation (mapping the CLI value to the
    AAP-side payload field). ``LaunchFlag`` collapses them into one
    row so adding a ninth flag is one tuple entry instead of four
    parallel edits.

    The inline ``payload_builder`` is a deliberate departure from the
    project's usual ``apply_strategy: str`` + resolver pattern (see
    :class:`ResourceSpec`). That pattern earns its keep by keeping
    ``domain/`` pure of behaviour selectors; ``LaunchFlag`` lives in
    ``cli/`` (composition root) where the closures are trivial and
    not independently injectable.
    """

    flag: str
    accepts_key: str
    payload_builder: Callable[[Any, FkResolver, dict[str, str] | None], Any]


# Source of truth for the launch CLI flag → payload-field mapping.
# ``extra_vars`` and ``limit`` stay outside the table — both are
# accepted by every launch-capable kind today, so they don't need
# per-kind visibility / rejection logic. If a future kind drops one,
# fold it in here.
LAUNCH_FLAGS: tuple[LaunchFlag, ...] = (
    LaunchFlag(
        "--inventory",
        "inventory",
        lambda v, fk, scope: fk.name_to_id("Inventory", v, scope=scope),
    ),
    LaunchFlag(
        "--credential",
        "credentials",
        lambda v, fk, scope: [fk.name_to_id("Credential", c, scope=scope) for c in v],
    ),
    LaunchFlag("--scm-branch", "scm_branch", lambda v, _fk, _scope: v),
    LaunchFlag("--job-tag", "job_tags", lambda v, _fk, _scope: ",".join(v)),
    LaunchFlag("--skip-tag", "skip_tags", lambda v, _fk, _scope: ",".join(v)),
    LaunchFlag("--verbosity", "verbosity", lambda v, _fk, _scope: v),
    LaunchFlag("--diff-mode", "diff_mode", lambda v, _fk, _scope: v),
    LaunchFlag("--job-type", "job_type", lambda v, _fk, _scope: v),
)


def _is_supplied(value: object) -> bool:
    # ``None`` (default) and ``[]`` (repeatable flag not given) mean
    # "not supplied". ``False`` (``--no-diff-mode``) and ``0``
    # (``--verbosity 0``) ARE supplied and must round-trip — so this
    # is not ``bool(value)``.
    return value is not None and value != []


def _reject_unsupported_launch_flags(
    *,
    kind: str,
    accepts: frozenset[str],
    supplied: dict[str, object],
) -> None:
    """Fail loudly when the user supplies a flag this kind doesn't accept.

    Avoids the "parser acknowledges, code silently ignores" footgun. The
    flags are wired uniformly across kinds (Typer signature is shared in
    ``_add_launch``) but a workflow template's ``launch.accepts`` is a
    strict subset of a job template's, and silently dropping a value the
    user typed deliberately would be worse than rejecting up front.
    """
    bad = sorted(
        f.flag
        for f in LAUNCH_FLAGS
        if _is_supplied(supplied.get(f.flag)) and f.accepts_key not in accepts
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
    supplied: dict[str, object],
    fk: FkResolver,
    org_scope: dict[str, str] | None,
) -> dict[str, Any]:
    """Translate the launch CLI flags into the payload AAP expects.

    Only fields listed in this kind's ``ActionSpec.accepts`` are
    forwarded; flags for fields not in ``accepts`` are silently
    ignored. FK flags (``--inventory``, ``--credential``) resolve
    names to ids using the per-process :class:`FkResolver` via each
    row's ``payload_builder``.
    """
    payload: dict[str, Any] = {}
    if extra_vars and "extra_vars" in accepts:
        payload["extra_vars"] = "\n".join(extra_vars)
    if limit and "limit" in accepts:
        payload["limit"] = limit
    for f in LAUNCH_FLAGS:
        if f.accepts_key not in accepts:
            continue
        value = supplied.get(f.flag)
        if not _is_supplied(value):
            continue
        payload[f.accepts_key] = f.payload_builder(value, fk, org_scope)
    return payload


def _echo_parallel_errors(errors: list[tuple[str, UntapedError]]) -> bool:
    """Echo per-job errors from a parallel-monitor helper and return
    ``True`` when any were recorded so the caller can flip its
    ``any_failed`` flag with ``|=``.
    """
    for failed_name, failure in errors:
        typer.echo(f"error: {failed_name}: {failure}", err=True)
    return bool(errors)


__all__ = ["LAUNCH_FLAGS", "LaunchFlag"]
