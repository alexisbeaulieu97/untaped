"""``delete`` builder for the spec-driven CLI factory."""

from typing import Annotated, Any

from cyclopts import App, Parameter

from untaped.api import (
    ColumnsOption,
    FormatOption,
    batch_apply,
    emit,
    finish,
    raise_usage,
    read_identifiers,
    report_errors,
    resolve_each,
)
from untaped.capabilities.awx.application import DeleteResource, GetResource
from untaped.capabilities.awx.application.get_resource import parse_resource_id
from untaped.capabilities.awx.cli._context import open_context, scope_for_command
from untaped.capabilities.awx.cli._pipe import id_field_for
from untaped.capabilities.awx.cli.options import (
    ByIdOption,
    InventoryLookupOption,
    InventoryOrganizationOption,
    OrganizationLookupOption,
)
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec


def _add_delete(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="delete")
    def delete_command(
        names: Annotated[list[str] | None, Parameter(help=f"{spec.kind} name(s).")] = None,
        *,
        stdin: Annotated[
            bool,
            Parameter(name="--stdin", negative="", help="Read names from stdin (one per line)."),
        ] = False,
        yes: Annotated[
            bool,
            Parameter(name=["--yes", "-y"], negative="", help="Skip the confirmation prompt."),
        ] = False,
        dry_run: Annotated[
            bool,
            Parameter(
                name="--dry-run",
                negative="",
                help="Resolve targets and print what would be deleted; don't call DELETE.",
            ),
        ] = False,
        organization: OrganizationLookupOption = None,
        inventory: InventoryLookupOption = None,
        inventory_organization: InventoryOrganizationOption = None,
        by_id: ByIdOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Delete one or more resources by name, or by explicit AWX id.

        ``--stdin`` reads newline-separated identifiers (names by
        default, ids with ``--by-id``). Refuses to consume stdin without
        ``--yes`` or ``--dry-run`` — can't prompt for confirmation while
        stdin is being read. Each successful delete emits a row whose
        first key is ``id``, so ``--format raw`` returns the deleted ids
        for downstream pipelines.
        """
        if not names and not stdin:
            raise_usage(f"provide {spec.kind} name(s) or --stdin")
        if stdin and not yes and not dry_run:
            raise_usage("--stdin requires --yes (skip confirmation) or --dry-run (preview only)")
        rows: list[dict[str, Any]] = []
        with report_errors(), open_context() as ctx:
            ids = read_identifiers(
                list(names or []), stdin=stdin, id_field=id_field_for(spec, by_id=by_id)
            )
            scope = scope_for_command(
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
            fast_path = yes and not dry_run and by_id
            prefetch = getter.by_ids(spec, ids) if fast_path else {}
            resolved, any_failed = resolve_each(
                ids,
                lambda n: _resolve_for_delete(
                    n,
                    spec=spec,
                    getter=getter,
                    scope=scope,
                    by_id=by_id,
                    fast_path=fast_path,
                    prefetch=prefetch,
                ),
            )
            deleter = DeleteResource(ctx.repo)
            # ``batch_apply`` owns the preview/confirm/--yes gate and the
            # per-id ``error: <ident>: <exc>`` loop. ``any_failed`` from the
            # resolve phase above is OR-ed in below so a prior resolve error
            # (or a declined prompt over a partial batch) still drives exit 1.
            outcome = batch_apply(
                resolved,
                lambda pair: _do_delete(deleter, spec, pair),
                verb="delete",
                noun=spec.kind,
                label=lambda pair: pair[0],
                describe=lambda pair: _delete_row(pair[1]),
                ui=ctx.progress_ui(),
                destructive=True,
                assume_yes=yes,
                preview_only=dry_run,
            )
            rows = (
                outcome.planned_rows
                if dry_run
                else [_delete_row(record, deleted=True) for _, record in outcome.results]
            )
            any_failed = any_failed or outcome.any_failed
        if rows:
            emit(rows, fmt=fmt, columns=columns, kind="awx.delete_outcome")
        finish(any_failed)


def _resolve_for_delete(
    n: str,
    *,
    spec: AwxResourceSpec,
    getter: GetResource,
    scope: dict[str, str] | None,
    by_id: bool,
    fast_path: bool,
    prefetch: dict[int, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Map an identifier to ``(identifier, record)``.

    On the numeric-id fast path, looks up the record in the caller's
    bulk-prefetched cache; on a miss returns a stub record with the id
    only — the upcoming DELETE confirms existence. Off the fast path
    (or for name-based identifiers) goes through the normal GET.
    """
    if fast_path:
        id_ = parse_resource_id(n)
        if hit := prefetch.get(id_):
            return n, hit
        return n, {"id": id_}
    return n, getter.by_identifier(spec, n, scope=scope, by_id=by_id)


def _delete_row(record: dict[str, Any], *, deleted: bool | None = None) -> dict[str, Any]:
    # First key is ``id`` so ``--format raw`` returns the (would-be-)deleted
    # id — preserves the pipe-friendly first-key contract used by every
    # spec-driven list/get command (see root AGENTS.md "--format raw" contract).
    # The ``deleted`` key is set only after a successful DELETE; dry-run
    # rows omit it so ``jq 'select(.deleted)'`` doesn't silently pick up
    # preview rows.
    row: dict[str, Any] = {"id": record.get("id"), "name": record.get("name", "")}
    if deleted is not None:
        row["deleted"] = deleted
    return row


def _do_delete(
    deleter: DeleteResource,
    spec: AwxResourceSpec,
    pair: tuple[str, dict[str, Any]],
) -> dict[str, Any]:
    """Delete one resolved ``(identifier, record)`` and return its record.

    Raises :class:`UntapedError` on a failed DELETE; ``batch_apply`` catches it
    and emits the ``error: <identifier>: <exc>`` row (``identifier`` is the
    ``label`` it was given).
    """
    _identifier, record = pair
    deleter(spec, int(record["id"]))
    return record
