"""Compose declarative file (or stdin) preparation with the shared CLI mutation gate."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from pathlib import Path

from untaped.capabilities.awx.application import BatchMutationEngine, prepare_apply_file
from untaped.capabilities.awx.cli._mutation_runner import (
    WriteControls,
    emit_outcomes,
    preview_and_execute,
    run_mutation_plan,
)
from untaped.capabilities.awx.cli.context import AwxContext
from untaped.capabilities.awx.domain import Resource
from untaped.capabilities.awx.infrastructure.git_source import GitSource
from untaped.capabilities.awx.infrastructure.yaml_io import (
    read_resource_files,
    read_resource_files_at,
    read_resource_text,
)
from untaped.sdk import ConfigError, resolve_text_input


def build_mutation_engine(
    ctx: AwxContext, *, allow_unverified: bool = False
) -> BatchMutationEngine:
    """Wire the authoritative application engine for all configuration commands."""
    return BatchMutationEngine(
        client=ctx.repo,
        catalog=ctx.catalog,
        fk=ctx.fk,
        strategies=ctx.strategies,
        warn=lambda msg: ctx.progress_ui().message("warning", msg),
        allow_unverified=allow_unverified,
        nodes=ctx.workflow_nodes,
    )


def with_default_organization(ctx: AwxContext, doc: Resource) -> Resource:
    """Scope org-less documents of org-scoped kinds by ``awx.default_organization``.

    Selection scopes the same way. An explicit ``metadata.organization`` (even
    ``null``) is kept; a ``spec.organization`` name is the identity otherwise.
    """
    if "organization" not in ctx.catalog.get(doc.kind).identity_keys:
        return doc
    if "organization" in doc.metadata.model_fields_set:
        return doc
    if "organization" in doc.spec:
        declared = doc.spec["organization"]
        if declared is not None and not isinstance(declared, str):
            return doc
        organization: str | None = declared
    elif ctx.default_organization is None:
        return doc
    else:
        organization = ctx.default_organization
    metadata = doc.metadata.model_copy(update={"organization": organization})
    return doc.model_copy(update={"metadata": metadata})


STDIN = Path("-")
"""``apply -``: read the YAML documents from stdin."""


def _read_documents(path: Path, source: GitSource | None) -> list[tuple[str, Resource]]:
    """Every document of ``path`` (a file, a directory, or ``-`` for stdin) and its source.

    With ``source``, ``path`` is read at its pinned commit, never the working tree.
    """
    if source is not None:
        return read_resource_files_at(source, path)
    if path != STDIN:
        return [(str(source), doc) for source, doc in read_resource_files(path)]
    empty = ConfigError("no YAML documents on stdin; pipe them into `apply -`", category="invalid")
    try:
        text = resolve_text_input(value=None, file=None, what="documents")
    except ConfigError:
        raise empty from None
    docs = [("<stdin>", doc) for doc in read_resource_text(text, source="<stdin>")]
    if not docs:
        raise empty
    return docs


def run_apply(
    ctx: AwxContext,
    files: Sequence[Path],
    controls: WriteControls,
    *,
    check: bool = False,
    source_ref: str | None = None,
) -> None:
    """Prepare once, confirm once, execute the same complete batch.

    ``check`` only computes the plan: nothing is written, and the command
    exits 3 when any document would change the controller. ``source_ref``
    reads every path at that ref of the current repository.
    """

    known = set(ctx.catalog.kinds())
    pinned = GitSource.resolve(source_ref) if source_ref is not None else None

    def reader(path: Path) -> Iterable[Resource]:
        docs = _read_documents(path, pinned)
        for source, doc in docs:
            # Name the file: a directory apply reads every *.yml and *.yaml.
            if doc.kind not in known:
                raise ConfigError(
                    f"{source}: unknown kind {doc.kind!r} (available: {', '.join(sorted(known))})",
                    category="invalid",
                )
        return [with_default_organization(ctx, doc) for _source, doc in docs]

    engine = build_mutation_engine(ctx, allow_unverified=controls.allow_unverified)
    plan = prepare_apply_file(engine, reader, files, catalog=ctx.catalog, fk=ctx.fk)
    if not check:
        run_mutation_plan(ctx, engine, plan, controls)
        return
    previews = preview_and_execute(ctx, engine, plan, replace(controls, dry_run=True))
    emit_outcomes(
        previews,
        fmt=controls.fmt,
        columns=controls.columns,
        predicate_hit=any(outcome.action != "unchanged" for outcome in previews),
    )
