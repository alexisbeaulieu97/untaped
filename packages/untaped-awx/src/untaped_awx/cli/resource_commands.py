"""Factory for per-resource Typer sub-apps."""

from __future__ import annotations

from collections.abc import Callable, Sequence
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
from untaped_awx.cli._apply_runner import run_apply
from untaped_awx.cli._context import open_context, scope_for_spec
from untaped_awx.cli._event_render import render_event_text
from untaped_awx.cli._names import flatten_fks
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
        builder = _ACTION_BUILDERS.get(action.name)
        if builder is not None:
            builder(app, spec)

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
        """List resources."""
        filters = parse_kv_pairs(filter_, flag="--filter")
        with report_errors(), open_context() as ctx:
            records = list(
                ListResources(ctx.repo)(spec, search=search, filters=filters, limit=limit)
            )
        cols = list(columns) if columns else list(spec.list_columns)
        if with_names:
            # Pass ``cols`` so display-only FK columns (e.g. Host's
            # ``inventory``, which lives in ``read_only_fields`` rather
            # than ``fk_refs``) get flattened from ``summary_fields``.
            records = flatten_fks(records, spec, columns=cols)
        typer.echo(format_output(records, fmt=fmt, columns=cols))


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
            for n in ids:
                try:
                    records.append(_get_one(getter, spec, n, scope, by_name=by_name))
                except UntapedError as exc:
                    typer.echo(f"error: {n}: {exc}", err=True)
                    any_failed = True
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


def _get_one(
    getter: GetResource,
    spec: AwxResourceSpec,
    identifier: str,
    scope: dict[str, str] | None,
    *,
    by_name: bool = False,
) -> dict[str, Any]:
    # `isdecimal()` matches Unicode category Nd — exactly the set
    # `int()` accepts. `isdigit()` admits superscripts/subscripts
    # like "²" that `int()` would reject with ValueError.
    if not by_name and identifier.isdecimal():
        return getter(spec, id_=int(identifier))
    return getter(spec, name=identifier, scope=scope)


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
            )


# ---- launch ----


def _add_launch(app: typer.Typer, spec: AwxResourceSpec) -> None:
    accepts = next((a.accepts for a in spec.actions if a.name == "launch"), frozenset())

    @app.command("launch", no_args_is_help=True)
    def launch_command(
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
            None, "--inventory", help="Override inventory by name (resolved to id)."
        ),
        credential: list[str] | None = typer.Option(
            None,
            "--credential",
            help="Override credential by name (repeatable; resolved to ids).",
        ),
        scm_branch: str | None = typer.Option(None, "--scm-branch", help="SCM branch to run from."),
        job_tag: list[str] | None = typer.Option(
            None, "--job-tag", help="Run only tasks with these tags (repeatable)."
        ),
        skip_tag: list[str] | None = typer.Option(
            None, "--skip-tag", help="Skip tasks with these tags (repeatable)."
        ),
        verbosity: int | None = typer.Option(None, "--verbosity", help="0-4 (passed verbatim)."),
        diff_mode: bool | None = typer.Option(
            None,
            "--diff-mode/--no-diff-mode",
            help="Override diff_mode for this run.",
        ),
        job_type: str | None = typer.Option(
            None, "--job-type", help="Override job_type (e.g. run, check)."
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
        jobs: list[Any] = []
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
            for n in ids:
                try:
                    job = RunAction(ctx.repo)(
                        spec,
                        name=n,
                        action="launch",
                        scope=scope,
                        payload=payload,
                    )
                    if track:
                        # Render each event to stderr as it lands, then
                        # let the monitor's terminal flip end the loop.
                        # ``track_console`` carries the TTY-aware colour
                        # styling so green-ok / red-failed pop in a real
                        # terminal but stay plain text when piped.
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
    forwarded; flags for fields not in ``accepts`` are silently ignored
    so a kind that doesn't support ``--inventory`` simply drops the
    value rather than erroring on input the user typed naturally.
    FK flags (``--inventory``, ``--credential``) resolve names to ids
    using the per-process :class:`FkResolver`.
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
    # Project's update declares accepts=frozenset(); when update grows
    # payload-bearing flags this builder will need the same accepts-driven
    # parameter sub-setting that _add_launch is slated to gain (see
    # _ACTION_BUILDERS comment block).
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
#
# Note on per-kind parameter sub-setting: every launch-capable kind
# currently exposes the *full* launch parser regardless of its
# ``ActionSpec.accepts``. Mismatched usage is caught loudly by
# :func:`_reject_unsupported_launch_flags`, so the user sees a clear
# "this kind doesn't accept --scm-branch" rather than a silent drop.
# Dynamically constructing per-kind Typer signatures (so unsupported
# flags don't appear in ``--help``) would require fragile
# ``inspect.Signature`` manipulation; deferred as a future improvement.
_ACTION_BUILDERS: dict[str, Callable[[typer.Typer, AwxResourceSpec], None]] = {
    "launch": _add_launch,
    "update": _add_update,
}
