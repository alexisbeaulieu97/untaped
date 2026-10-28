"""What the export commands share: documents written as YAML, printed or saved to files."""

from __future__ import annotations

import stat
from collections.abc import Iterator
from pathlib import Path

from untaped.sdk import UntapedError, atomic_write
from untaped_awx.application import SaveResource, SaveResources
from untaped_awx.application.resources.selection import SelectedResource
from untaped_awx.commands.context import AwxContext
from untaped_awx.document import Document
from untaped_awx.domain import Resource, ResourceSpec
from untaped_awx.errors import AwxApiError
from untaped_awx.infrastructure.resources.yaml_io import dump_resource


def document_row(resource: Resource, text: str, *, path: Path | None = None) -> Document:
    """``resource`` as the row an export prints, carrying the YAML it was written as."""
    row = Document.model_validate(resource.model_dump(mode="json", exclude_none=True))
    row._text = text
    row._path = path
    return row


def yaml_text(rows: Document | list[Document]) -> str | None:
    """``--format yaml``: the documents as one multi-document stream ``apply -`` reads."""
    documents = [rows] if isinstance(rows, Document) else rows
    return "---\n".join(row._text or _dumped(row) for row in documents) or None


def path_lines(rows: Document | list[Document]) -> list[str]:
    """``--format paths``: the file each document was written to, one per line."""
    documents = [rows] if isinstance(rows, Document) else rows
    return [str(row._path) for row in documents if row._path is not None]


def _dumped(row: Document) -> str:
    return dump_resource(Resource.model_validate(row.model_dump(mode="json")))


def save_selection(
    ctx: AwxContext,
    spec: ResourceSpec,
    selected: tuple[SelectedResource, ...],
    *,
    out: Path | None,
    comment: str | None = None,
) -> list[Document]:
    """Export selected records without resolving their names again.

    With ``out`` (not ``-``) the documents are written there and no row is
    returned; otherwise each document is a row.
    """
    saver = SaveResource(ctx.repo, ctx.fk, nodes=ctx.workflow_nodes, warn=ctx.diag.warning)
    resources = [saver.from_record(spec, item.record) for item in selected]
    fidelity_comment = spec.fidelity_note if spec.fidelity != "full" else None
    if fidelity_comment:
        ctx.diag.info(f"{spec.fidelity} save: {fidelity_comment}")
    texts = [
        dump_resource(resource, header_comment=fidelity_comment, comment=comment)
        for resource in resources
    ]
    if out and str(out) != "-":
        write_output(out, "---\n".join(texts))
        return []
    return [document_row(resource, text) for resource, text in zip(resources, texts, strict=True)]


def save_batch(
    ctx: AwxContext,
    *,
    out_dir: Path,
    all_kinds: bool,
    kind: str | None,
    filters: dict[str, str],
    organization: str | None,
    comment: str | None = None,
) -> Iterator[Document]:
    """Bulk-save resources to disk, one file each, yielding each written document."""
    outcomes = SaveResources(
        ctx.repo, ctx.fk, ctx.catalog, nodes=ctx.workflow_nodes, warn=ctx.diag.warning
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
            ctx.diag.warning(f"{outcome.kind}: skipped, {outcome.detail}")
            continue
        if outcome.resource is None or outcome.filename is None:  # pragma: no cover
            raise AwxApiError(f"invalid save outcome for {outcome.kind}: missing resource")
        target = out_dir / outcome.filename
        _assert_inside(out_dir, target)
        text = dump_resource(
            outcome.resource, header_comment=outcome.header_comment, comment=comment
        )
        write_output(target, text)
        yield document_row(outcome.resource, text, path=target)


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
