"""Shared save runner for top-level and per-resource AWX save commands."""

from __future__ import annotations

import stat
from collections.abc import Callable
from pathlib import Path

from untaped.sdk import OutputFormat, UntapedError, atomic_write, echo, emit
from untaped_awx.application import SaveResource, SaveResources
from untaped_awx.application.selection import SelectedResource
from untaped_awx.cli.context import AwxContext
from untaped_awx.domain import ResourceSpec
from untaped_awx.errors import AwxApiError
from untaped_awx.infrastructure.yaml_io import dump_resource


def run_save_selection(
    ctx: AwxContext,
    spec: ResourceSpec,
    selected: tuple[SelectedResource, ...],
    *,
    output: Path | None,
    fmt: OutputFormat,
    columns: list[str] | None,
    comment: str | None = None,
) -> None:
    """Export selected records without resolving their names again."""
    saver = SaveResource(ctx.repo, ctx.fk, nodes=ctx.workflow_nodes, warn=_warner(ctx))
    resources = [saver.from_record(spec, item.record) for item in selected]
    fidelity_comment = spec.fidelity_note if spec.fidelity != "full" else None
    if fidelity_comment:
        echo(f"{spec.fidelity} save: {fidelity_comment}", err=True)
    text = "---\n".join(
        dump_resource(resource, header_comment=fidelity_comment, comment=comment)
        for resource in resources
    )
    if output and str(output) != "-":
        write_output(output, text)
    elif output or fmt == "yaml":
        if text:
            echo(text)
    else:
        envelopes = [resource.model_dump(exclude_none=True) for resource in resources]
        emit(
            envelopes[0] if len(envelopes) == 1 else envelopes,
            fmt=fmt,
            columns=columns,
            kind="awx.document",
        )


def run_save_batch(
    ctx: AwxContext,
    *,
    out_dir: Path,
    all_kinds: bool,
    kind: str | None,
    filters: dict[str, str],
    organization: str | None,
    print_paths: bool,
    comment: str | None = None,
) -> None:
    """Bulk-save resources to disk and write the requested stdout shape."""
    outcomes = SaveResources(
        ctx.repo, ctx.fk, ctx.catalog, nodes=ctx.workflow_nodes, warn=_warner(ctx)
    )(
        all_kinds=all_kinds,
        kind=kind,
        filters=filters,
        organization=organization,
    )
    out_dir = out_dir.expanduser()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UntapedError(f"cannot create {out_dir}: {exc.strerror or exc}") from exc
    for outcome in outcomes:
        if outcome.action == "skipped":
            ctx.progress_ui().message("warning", f"{outcome.kind}: skipped, {outcome.detail}")
            continue
        if outcome.resource is None or outcome.filename is None:  # pragma: no cover
            raise AwxApiError(f"invalid save outcome for {outcome.kind}: missing resource")
        target = out_dir / outcome.filename
        _assert_inside(out_dir, target)
        text = dump_resource(
            outcome.resource, header_comment=outcome.header_comment, comment=comment
        )
        write_output(target, text)
        if print_paths:
            echo(str(target))
        else:
            echo("---")
            echo(text)


def _warner(ctx: AwxContext) -> Callable[[str], None]:
    return lambda message: ctx.progress_ui().message("warning", message)


def write_output(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` without replacing what the path points at.

    Regular files are replaced atomically, through a symlink when ``path`` is
    one (the link survives). Existing non-regular targets such as FIFOs or
    ``/dev/stdout`` are written directly. OS errors become an
    :class:`UntapedError` naming the path instead of a traceback.
    """
    target = path.expanduser()
    try:
        if target.is_symlink():
            target = target.resolve()
        if target.exists() and not stat.S_ISREG(target.stat().st_mode):
            with target.open("w", encoding="utf-8", newline="") as handle:
                handle.write(text)
        else:
            atomic_write(target, text)
    except OSError as exc:
        raise UntapedError(f"cannot write {path}: {exc.strerror or exc}") from exc


def _assert_inside(parent: Path, target: Path) -> None:
    """Refuse paths that resolve outside the intended parent directory."""
    parent_resolved = parent.resolve()
    try:
        target.resolve().relative_to(parent_resolved)
    except ValueError as exc:  # pragma: no cover - defensive guard
        raise AwxApiError(f"refusing to write {target} — outside {parent_resolved}") from exc
