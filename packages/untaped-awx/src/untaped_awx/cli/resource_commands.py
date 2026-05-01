"""Factory for per-resource Typer sub-apps."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from untaped_core import (
    OutputFormat,
    UntapedError,
    format_output,
    read_identifiers,
    report_errors,
)

from untaped_awx.application import (
    GetResource,
    ListResources,
    RunAction,
    SaveResource,
    WatchJob,
)
from untaped_awx.cli._apply_runner import run_apply
from untaped_awx.cli._context import open_context
from untaped_awx.domain import ResourceSpec
from untaped_awx.infrastructure.yaml_io import dump_resource, write_resource


def make_resource_app(spec: ResourceSpec) -> typer.Typer:
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
        if action.name == "launch":
            _add_launch(app, spec)
        elif action.name == "update":
            _add_update(app, spec)

    return app


# ---- list ----


def _add_list(app: typer.Typer, spec: ResourceSpec) -> None:
    @app.command("list")
    def list_command(
        search: str | None = typer.Option(None, "--search", help="Fuzzy server-side search."),
        organization: str | None = typer.Option(
            None, "--organization", help="Filter by organization name."
        ),
        limit: int | None = typer.Option(None, "--limit", help="Cap result count."),
        fmt: OutputFormat = typer.Option("table", "--format", "-f", help="Output format."),
        columns: list[str] | None = typer.Option(
            None, "--columns", "-c", help="Columns to include (repeatable)."
        ),
    ) -> None:
        """List resources."""
        with report_errors(), open_context() as ctx:
            scope = _scope(ctx, organization, spec)
            records = list(ListResources(ctx.repo)(spec, search=search, scope=scope, limit=limit))
        cols = list(columns) if columns else list(spec.list_columns)
        typer.echo(format_output(records, fmt=fmt, columns=cols))


# ---- get ----


def _add_get(app: typer.Typer, spec: ResourceSpec) -> None:
    @app.command("get", no_args_is_help=True)
    def get_command(
        names: list[str] | None = typer.Argument(None, help=f"{spec.kind} name(s)."),
        stdin: bool = typer.Option(False, "--stdin", help="Read names from stdin (one per line)."),
        organization: str | None = typer.Option(
            None, "--organization", help="Scope to organization."
        ),
        fmt: OutputFormat = typer.Option("yaml", "--format", "-f"),
        columns: list[str] | None = typer.Option(None, "--columns", "-c"),
    ) -> None:
        """Fetch one or more resources by name."""
        records: list[Any] = []
        any_failed = False
        with report_errors(), open_context() as ctx:
            ids = read_identifiers(list(names or []), stdin=stdin)
            scope = _scope(ctx, organization, spec)
            for n in ids:
                try:
                    records.append(GetResource(ctx.repo)(spec, name=n, scope=scope))
                except UntapedError as exc:
                    typer.echo(f"error: {n}: {exc}", err=True)
                    any_failed = True
        if records:
            typer.echo(format_output(records, fmt=fmt, columns=list(columns or [])))
        if any_failed:
            raise typer.Exit(code=1)


# ---- save ----


def _add_save(app: typer.Typer, spec: ResourceSpec) -> None:
    @app.command("save", no_args_is_help=True)
    def save_command(
        name: str = typer.Argument(..., help=f"{spec.kind} name."),
        output: Path | None = typer.Option(
            None, "--out", "-o", help="Write to FILE; default is stdout."
        ),
        organization: str | None = typer.Option(
            None, "--organization", help="Scope to organization."
        ),
    ) -> None:
        """Dump the resource as a portable YAML envelope."""
        with report_errors(), open_context() as ctx:
            scope = _scope(ctx, organization, spec)
            resource = SaveResource(ctx.repo, ctx.fk)(spec, name=name, scope=scope)
        comment = spec.fidelity_note if spec.fidelity != "full" else None
        if comment:
            typer.echo(f"{spec.fidelity} save: {comment}", err=True)
        if output:
            write_resource(output, resource, header_comment=comment)
        else:
            typer.echo(dump_resource(resource, header_comment=comment))


# ---- apply (per-kind, single file) ----


def _add_apply(app: typer.Typer, spec: ResourceSpec) -> None:
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


def _add_launch(app: typer.Typer, spec: ResourceSpec) -> None:
    accepts = next((a.accepts for a in spec.actions if a.name == "launch"), frozenset())

    @app.command("launch", no_args_is_help=True)
    def launch_command(
        names: list[str] | None = typer.Argument(None, help=f"{spec.kind} name(s)."),
        stdin: bool = typer.Option(False, "--stdin", help="Read names from stdin (one per line)."),
        organization: str | None = typer.Option(
            None, "--organization", help="Scope to organization."
        ),
        extra_vars: list[str] | None = typer.Option(
            None,
            "--extra-vars",
            help="KEY=VAL override (repeatable).",
        ),
        limit: str | None = typer.Option(None, "--limit", help="Hosts pattern to limit to."),
        wait: bool = typer.Option(False, "--wait", help="Block until terminal."),
        monitor: bool = typer.Option(
            False, "--monitor", help="Stream + wait (alias for --wait in v0)."
        ),
    ) -> None:
        """Launch one or more resources and (optionally) wait for each job."""
        payload: dict[str, Any] = {}
        if extra_vars and "extra_vars" in accepts:
            payload["extra_vars"] = "\n".join(extra_vars)
        if limit and "limit" in accepts:
            payload["limit"] = limit
        jobs: list[Any] = []
        any_failed = False
        with report_errors(), open_context() as ctx:
            ids = read_identifiers(list(names or []), stdin=stdin)
            scope = _scope(ctx, organization, spec)
            for n in ids:
                try:
                    job = RunAction(ctx.repo)(
                        spec,
                        name=n,
                        action="launch",
                        scope=scope,
                        payload=payload,
                    )
                    if wait or monitor:
                        job = WatchJob(ctx.repo)(job)
                    jobs.append(job)
                except UntapedError as exc:
                    typer.echo(f"error: {n}: {exc}", err=True)
                    any_failed = True
        if jobs:
            typer.echo(format_output([j.model_dump() for j in jobs], fmt="yaml", columns=[]))
        if any_failed:
            raise typer.Exit(code=1)


# ---- update (Project SCM sync) ----


def _add_update(app: typer.Typer, spec: ResourceSpec) -> None:
    @app.command("update", no_args_is_help=True)
    def update_command(
        name: str = typer.Argument(..., help=f"{spec.kind} name."),
        organization: str | None = typer.Option(
            None, "--organization", help="Scope to organization."
        ),
        wait: bool = typer.Option(False, "--wait", help="Block until terminal."),
    ) -> None:
        """Trigger an SCM sync (Project)."""
        with report_errors(), open_context() as ctx:
            scope = _scope(ctx, organization, spec)
            job = RunAction(ctx.repo)(spec, name=name, action="update", scope=scope)
            if wait:
                job = WatchJob(ctx.repo)(job)
        typer.echo(format_output([job.model_dump()], fmt="yaml", columns=[]))


# ---- helpers ----


def _scope(ctx: Any, organization: str | None, spec: ResourceSpec) -> dict[str, str] | None:
    # Org-scoping only applies to specs whose identity includes ``organization``.
    # Global resources (Organization, CredentialType) and parent-scoped ones
    # (Schedule) must not pick up ``awx.default_organization`` as a filter —
    # AWX would interpret ``organization__name=...`` against records that have
    # no such column and silently return zero results.
    if "organization" not in spec.identity_keys:
        return None
    org = organization or ctx.default_organization
    return {"organization": org} if org else None
