"""Shared apply runner used by top-level ``awx apply`` and per-resource
``awx <kind> apply``.

Composition root: imports the YAML reader from infrastructure, wraps it
with optional kind-filter logic, and hands the result to the
application-layer :class:`ApplyFile` use case (which only sees a
``ResourceDocumentReader`` Protocol).
"""

from collections.abc import Iterable
from pathlib import Path

from untaped.api import (
    OutputFormat,
    UntapedError,
    clamp_parallel,
    echo,
    emit,
    finish,
    raise_usage,
    read_identifiers,
    resolve_each,
)
from untaped.capabilities.awx.application import ApplyFile, ApplyResource, GetResource
from untaped.capabilities.awx.application.apply_file import APPLY_PARALLEL_CAP
from untaped.capabilities.awx.application.apply_planner import unrecognized_warning
from untaped.capabilities.awx.application.ports import ResourceDocumentReader
from untaped.capabilities.awx.cli._context import AwxContext, scope_for_command
from untaped.capabilities.awx.cli._overlay import build_overlay
from untaped.capabilities.awx.cli._pipe import id_field_for
from untaped.capabilities.awx.cli.format import diff_lines, outcome_rows
from untaped.capabilities.awx.domain import ApplyOutcome, IdentityRef, Metadata, Resource
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec
from untaped.capabilities.awx.infrastructure.yaml_io import read_resources


def run_apply(
    ctx: AwxContext,
    file: Path,
    *,
    write: bool,
    fail_fast: bool,
    allow_unverified: bool = False,
    fmt: OutputFormat = "table",
    columns: list[str] | None = None,
    kind_filter: str | None = None,
    cli_name: str | None = None,
    parallel: int = 1,
) -> None:
    """End-to-end apply for one CLI invocation. Writes to stdout/stderr."""
    if parallel < 1:
        raise_usage("--parallel must be >= 1")
    parallel = clamp_parallel(
        parallel, cap=APPLY_PARALLEL_CAP, policy="httpx.Limits.max_connections=10"
    )
    reader = _make_reader(kind_filter=kind_filter, cli_name=cli_name)
    apply_one = _build_apply_resource(ctx, allow_unverified=allow_unverified)
    outcomes = ApplyFile(apply_one, reader, ctx.catalog, ctx.fk, parallel=parallel)(
        file, write=write, fail_fast=fail_fast
    )
    emit(outcome_rows(outcomes), fmt=fmt, columns=columns, kind="awx.apply_outcome")
    if not write:
        for outcome in outcomes:
            for line in diff_lines(outcome):
                echo(line, err=True)
    finish(any(o.action in {"failed", "partial", "conflict", "skipped"} for o in outcomes))


def run_apply_stdin(
    ctx: AwxContext,
    spec: AwxResourceSpec,
    *,
    write: bool,
    set_pairs: list[str] | None,
    patch_file: Path | None,
    by_id: bool,
    allow_unverified: bool = False,
    organization: str | None = None,
    inventory: str | None = None,
    inventory_organization: str | None = None,
    fmt: OutputFormat = "table",
    columns: list[str] | None = None,
) -> None:
    """Mass-patch a piped selection: resolve each target, overlay, apply.

    The overlay (``--set`` + ``--patch-file``) becomes a synthetic
    ``Resource.spec`` per resolved item, run through
    :meth:`ApplyResource.apply_to_existing` — so it reuses the file-apply
    diff / secret-guard / FK machinery and never creates. Preview by default;
    ``write`` (``--yes``) issues the sparse PATCH. Writes to stdout/stderr.
    """
    overlay = build_overlay(set_pairs, patch_file)
    # Passthrough model: send unknown field(s) anyway (version-robust), but warn
    # once over the shared overlay so a typo / unknown field stays visible.
    warning = unrecognized_warning(spec, overlay)
    if warning is not None:
        echo(f"warning: {warning}", err=True)

    ids = read_identifiers([], stdin=True, id_field=id_field_for(spec, by_id=by_id))
    scope = scope_for_command(
        ctx, organization, spec, inventory=inventory, inventory_organization=inventory_organization
    )
    getter = GetResource(ctx.repo)
    resolved, any_failed = resolve_each(
        ids,
        lambda ident: (ident, getter.by_identifier(spec, ident, scope=scope, by_id=by_id)),
    )
    apply_one = _build_apply_resource(ctx, allow_unverified=allow_unverified)
    outcomes: list[ApplyOutcome] = []
    for ident, record in resolved:
        resource = Resource(
            kind=spec.kind,
            metadata=_metadata_for_stdin(spec, ident, record, scope),
            spec=dict(overlay),
        )
        try:
            outcomes.append(apply_one.apply_to_existing(resource, record, write=write))
        except UntapedError as exc:
            echo(f"error: {ident}: {exc}", err=True)
            any_failed = True

    if outcomes:
        rows = outcome_rows(outcomes)
        emit(rows, fmt=fmt, columns=columns, kind="awx.apply_outcome")
    if not write:
        for outcome in outcomes:
            for line in diff_lines(outcome):
                echo(line, err=True)
    finish(
        any_failed
        or any(o.action in {"failed", "partial", "conflict", "skipped"} for o in outcomes)
    )


def _make_reader(*, kind_filter: str | None, cli_name: str | None) -> ResourceDocumentReader:
    """Build a ResourceDocumentReader that optionally filters by kind."""

    def _reader(path: Path) -> Iterable[Resource]:
        docs = list(read_resources(path))
        if kind_filter is None:
            return docs
        wrong = [d for d in docs if d.kind != kind_filter]
        if wrong:
            unique = sorted({d.kind for d in wrong})
            label = cli_name or kind_filter
            echo(
                f"warning: {len(wrong)} doc(s) skipped — wrong kind for "
                f"{label} ({{{','.join(unique)}}})",
                err=True,
            )
        return [d for d in docs if d.kind == kind_filter]

    return _reader


def _metadata_for_stdin(
    spec: AwxResourceSpec,
    ident: str,
    record: dict[str, object],
    scope: dict[str, str] | None,
) -> Metadata:
    name = str(record.get("name", ident))
    if spec.apply_strategy == "inventory_child":
        inventory = (scope or {}).get("inventory") or _record_inventory_name(record)
        inventory_org = (scope or {}).get("inventory__organization") or _record_inventory_org(
            record
        )
        if inventory:
            return Metadata(
                name=name,
                parent=IdentityRef(kind="Inventory", name=inventory, organization=inventory_org),
            )
        return Metadata(name=name)
    organization = None
    if "organization" in spec.identity_keys:
        organization = (scope or {}).get("organization") or _record_organization_name(record)
    return Metadata(name=name, organization=organization)


def _record_organization_name(record: dict[str, object]) -> str | None:
    value = record.get("organization_name")
    if isinstance(value, str) and value:
        return value
    summary = record.get("summary_fields")
    if isinstance(summary, dict):
        org = summary.get("organization")
        if isinstance(org, dict):
            name = org.get("name")
            if isinstance(name, str) and name:
                return name
    return None


def _record_inventory_name(record: dict[str, object]) -> str | None:
    value = record.get("inventory_name")
    if isinstance(value, str) and value:
        return value
    inventory = _record_inventory_summary(record)
    if inventory is None:
        return None
    name = inventory.get("name")
    return name if isinstance(name, str) and name else None


def _record_inventory_org(record: dict[str, object]) -> str | None:
    inventory = _record_inventory_summary(record)
    if inventory is None:
        return None
    name = inventory.get("organization_name")
    return name if isinstance(name, str) and name else None


def _record_inventory_summary(record: dict[str, object]) -> dict[str, object] | None:
    summary = record.get("summary_fields")
    if not isinstance(summary, dict):
        return None
    inventory = summary.get("inventory")
    return inventory if isinstance(inventory, dict) else None


def _build_apply_resource(ctx: AwxContext, *, allow_unverified: bool = False) -> ApplyResource:
    def _warn(msg: str) -> None:
        echo(f"warning: {msg}", err=True)

    return ApplyResource(
        client=ctx.repo,
        catalog=ctx.catalog,
        fk=ctx.fk,
        strategies=ctx.strategies,
        warn=_warn,
        allow_unverified=allow_unverified,
    )


__all__ = ["run_apply", "run_apply_stdin"]
