"""``save`` builder for the spec-driven CLI factory."""

from __future__ import annotations

from pathlib import Path

import typer
from untaped_core import (
    ColumnsOption,
    FormatOption,
    format_output,
    report_errors,
)

from untaped_awx.application import SaveResource
from untaped_awx.cli._context import open_context, scope_for_command
from untaped_awx.infrastructure.spec import AwxResourceSpec
from untaped_awx.infrastructure.yaml_io import dump_resource, write_resource


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
        fmt: FormatOption = "yaml",
        columns: ColumnsOption = None,
    ) -> None:
        """Dump the resource as a portable YAML envelope.

        Default ``--format yaml`` emits the bare envelope so the output
        pipes straight into ``apply`` (multi-doc mapping shape that
        ``read_resources`` ingests). Non-yaml formats go through
        ``format_output`` for a one-row projection that matches the
        suite-wide ``--columns`` contract. ``--columns`` applies to
        non-yaml formats only — yaml emits the bare envelope unfiltered
        so the round-trip into ``apply`` stays intact.
        """
        with report_errors(), open_context() as ctx:
            scope = scope_for_command(
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
            return
        if fmt == "yaml":
            # Bypass format_output: apply's read_resources rejects
            # list-wrapped docs.
            typer.echo(dump_resource(resource, header_comment=comment))
            return
        # ``exclude_none=True`` keeps json/raw's projected fields in
        # sync with yaml (which goes through ``dump_resource``'s
        # ``exclude_none`` path), so the same envelope renders the same
        # field set across formats.
        envelope = resource.model_dump(exclude_none=True)
        typer.echo(format_output([envelope], fmt=fmt, columns=columns))
