"""``delete`` builder for the spec-driven CLI factory."""

from __future__ import annotations

from typing import Any

import typer
from untaped_core import (
    ColumnsOption,
    FormatOption,
    UntapedError,
    format_output,
    read_identifiers,
    report_errors,
    resolve_each,
)

from untaped_awx.application import DeleteResource, GetResource
from untaped_awx.cli._context import _scope, open_context
from untaped_awx.infrastructure.spec import AwxResourceSpec


def _add_delete(app: typer.Typer, spec: AwxResourceSpec) -> None:
    @app.command("delete", no_args_is_help=True)
    def delete_command(
        names: list[str] | None = typer.Argument(
            None, help=f"{spec.kind} name(s) or numeric id(s)."
        ),
        stdin: bool = typer.Option(
            False, "--stdin", help="Read names or numeric ids from stdin (one per line)."
        ),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        dry_run: bool = typer.Option(
            False,
            "--dry-run",
            help="Resolve targets and print what would be deleted; don't call DELETE.",
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
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Delete one or more resources by name or numeric id.

        ``--stdin`` reads newline-separated identifiers (same shape as
        ``get --stdin``). Refuses to consume stdin without ``--yes`` or
        ``--dry-run`` — can't prompt for confirmation while stdin is
        being read. Each successful delete emits a row whose first key
        is ``id``, so ``--format raw`` returns the deleted ids for
        downstream pipelines.
        """
        if stdin and not yes and not dry_run:
            raise typer.BadParameter(
                "--stdin requires --yes (skip confirmation) or --dry-run (preview only)"
            )
        rows: list[dict[str, Any]] = []
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
            # Under ``--yes`` (no preview to surface the name) we skip the
            # per-id resolve GET — AWX's DELETE returns the same
            # ``not found: <url>`` shape on a missing id. One bulk
            # ``?id__in=…`` keeps the ``name`` column populated.
            fast_path = yes and not dry_run
            prefetch = getter.by_ids(spec, ids) if fast_path and not by_name else {}
            resolved, any_failed = resolve_each(
                ids,
                lambda n: _resolve_for_delete(
                    n,
                    spec=spec,
                    getter=getter,
                    scope=scope,
                    by_name=by_name,
                    fast_path=fast_path,
                    prefetch=prefetch,
                ),
            )
            if dry_run:
                rows = [_delete_row(record) for _, record in resolved]
            elif resolved and _confirm_delete(resolved, spec, yes=yes):
                # Flow through to the post-``with`` checks on decline so
                # ``any_failed`` from a prior resolve error still drives
                # the exit code — an early ``return`` here would mask it.
                deleter = DeleteResource(ctx.repo)
                for identifier, record in resolved:
                    try:
                        deleter(spec, int(record["id"]))
                        rows.append(_delete_row(record, deleted=True))
                    except UntapedError as exc:
                        typer.echo(f"error: {identifier}: {exc}", err=True)
                        any_failed = True
        if rows:
            typer.echo(format_output(rows, fmt=fmt, columns=columns))
        if any_failed:
            raise typer.Exit(code=1)


def _resolve_for_delete(
    n: str,
    *,
    spec: AwxResourceSpec,
    getter: GetResource,
    scope: dict[str, str] | None,
    by_name: bool,
    fast_path: bool,
    prefetch: dict[int, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Map an identifier to ``(identifier, record)``.

    On the numeric-id fast path, looks up the record in the caller's
    bulk-prefetched cache; on a miss returns a stub record with the id
    only — the upcoming DELETE confirms existence. Off the fast path
    (or for name-based identifiers) goes through the normal GET.
    """
    if fast_path and not by_name and n.isdecimal():
        if hit := prefetch.get(int(n)):
            return n, hit
        return n, {"id": int(n)}
    return n, getter.by_identifier(spec, n, scope=scope, by_name=by_name)


def _delete_row(record: dict[str, Any], *, deleted: bool | None = None) -> dict[str, Any]:
    # First key is ``id`` so ``--format raw`` returns the (would-be-)deleted
    # id — preserves the pipe-friendly first-key contract used by every
    # spec-driven list/get command (see untaped-core "--format raw" contract).
    # The ``deleted`` key is set only after a successful DELETE; dry-run
    # rows omit it so ``jq 'select(.deleted)'`` doesn't silently pick up
    # preview rows.
    row: dict[str, Any] = {"id": record.get("id"), "name": record.get("name", "")}
    if deleted is not None:
        row["deleted"] = deleted
    return row


def _confirm_delete(
    resolved: list[tuple[str, dict[str, Any]]],
    spec: AwxResourceSpec,
    *,
    yes: bool,
) -> bool:
    """Print resolved targets to stderr and prompt; return user's choice.

    Matches ``untaped-workspace``'s ``_confirm(prompt, yes=…)`` shape so
    the same gating idiom reads consistently across domains.
    """
    if yes:
        return True
    typer.echo(f"About to delete {len(resolved)} {spec.kind}(s):", err=True)
    for _, record in resolved:
        typer.echo(f"  - {record.get('id')}\t{record.get('name', '')}", err=True)
    return typer.confirm("Continue?", default=False, err=True)
