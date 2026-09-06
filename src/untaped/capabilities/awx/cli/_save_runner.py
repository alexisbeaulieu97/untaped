"""Shared save runner for top-level and per-resource AWX save commands."""

from pathlib import Path

from untaped.api import OutputFormat, echo, emit
from untaped.capabilities.awx.application import SaveResource, SaveResources
from untaped.capabilities.awx.cli._context import AwxContext
from untaped.capabilities.awx.domain import ResourceSpec
from untaped.capabilities.awx.errors import AwxApiError
from untaped.capabilities.awx.infrastructure.yaml_io import dump_resource, write_resource


def run_save_one(
    ctx: AwxContext,
    spec: ResourceSpec,
    *,
    name: str,
    scope: dict[str, str] | None,
    output: Path | None,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    """Save one resource and write the requested CLI output."""
    resource = SaveResource(ctx.repo, ctx.fk)(spec, name=name, scope=scope)
    comment = spec.fidelity_note if spec.fidelity != "full" else None
    if comment:
        echo(f"{spec.fidelity} save: {comment}", err=True)
    if output:
        write_resource(output, resource, header_comment=comment)
        return
    if fmt == "yaml":
        # Bypass row rendering: apply's read_resources rejects list-wrapped docs.
        echo(dump_resource(resource, header_comment=comment))
        return
    envelope = resource.model_dump(exclude_none=True)
    emit(envelope, fmt=fmt, columns=columns, kind="awx.document")


def run_save_batch(
    ctx: AwxContext,
    *,
    out_dir: Path,
    all_kinds: bool,
    kind: str | None,
    filters: dict[str, str],
    organization: str | None,
    print_paths: bool,
) -> None:
    """Bulk-save resources to disk and write the requested stdout shape."""
    outcomes = SaveResources(ctx.repo, ctx.fk, ctx.catalog)(
        all_kinds=all_kinds,
        kind=kind,
        filters=filters,
        organization=organization,
    )
    out_dir = out_dir.expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    for outcome in outcomes:
        if outcome.action == "skipped":
            echo(f"skipping {outcome.kind}: {outcome.detail}", err=True)
            continue
        if outcome.resource is None or outcome.filename is None:  # pragma: no cover
            raise AwxApiError(f"invalid save outcome for {outcome.kind}: missing resource")
        target = out_dir / outcome.filename
        _assert_inside(out_dir, target)
        text = dump_resource(outcome.resource, header_comment=outcome.header_comment)
        target.write_text(text)
        if print_paths:
            echo(str(target))
        else:
            echo("---")
            echo(text)


def _assert_inside(parent: Path, target: Path) -> None:
    """Refuse paths that resolve outside the intended parent directory."""
    parent_resolved = parent.resolve()
    try:
        target.resolve().relative_to(parent_resolved)
    except ValueError as exc:  # pragma: no cover - defensive guard
        raise AwxApiError(f"refusing to write {target} — outside {parent_resolved}") from exc
