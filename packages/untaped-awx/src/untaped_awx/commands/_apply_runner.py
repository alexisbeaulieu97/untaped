"""Compose declarative file (or stdin) preparation with the shared CLI mutation gate."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import replace
from pathlib import Path

from untaped.sdk import ConfigError
from untaped_awx.application import BatchMutationEngine, prepare_apply_file
from untaped_awx.commands._mutation_runner import (
    WriteGate,
    outcome_results,
    preview_and_execute,
    run_mutation_plan,
)
from untaped_awx.commands.context import AwxContext
from untaped_awx.domain import Resource
from untaped_awx.infrastructure.git_source import GitSource
from untaped_awx.infrastructure.resources.yaml_io import (
    read_resource_files,
    read_resource_files_at,
    read_resource_text,
)
from untaped_awx.records import ApplyOutcomeRow


def build_mutation_engine(
    ctx: AwxContext, *, allow_unverified: bool = False
) -> BatchMutationEngine:
    """Wire the authoritative application engine for all configuration commands."""
    return BatchMutationEngine(
        client=ctx.repo,
        catalog=ctx.catalog,
        fk=ctx.fk,
        strategies=ctx.strategies,
        warn=ctx.diag.warning,
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


def _read_documents(
    path: Path, source: GitSource | None, stdin: Callable[[], str]
) -> list[tuple[str, Resource]]:
    """Every document of ``path`` (a file, a directory, or ``-`` for stdin) and its source.

    With ``source``, ``path`` is read at its pinned commit, never the working tree;
    ``stdin`` reads the documents ``-`` names.
    """
    if source is not None:
        return read_resource_files_at(source, path)
    if path != STDIN:
        return [(str(source), doc) for source, doc in read_resource_files(path)]
    empty = ConfigError("no YAML documents on stdin; pipe them into `apply -`", category="invalid")
    docs = [("<stdin>", doc) for doc in read_resource_text(stdin(), source="<stdin>")]
    if not docs:
        raise empty
    return docs


def run_apply(
    ctx: AwxContext,
    files: Sequence[Path],
    gate: WriteGate,
    *,
    stdin: Callable[[], str],
    check: bool = False,
    source_ref: str | None = None,
) -> Iterator[ApplyOutcomeRow]:
    """Prepare once, confirm once, execute the same complete batch.

    ``check`` only computes the plan: nothing is written, and the run is
    :class:`~untaped.sdk.Matched` (exit 3) when any document would change the
    controller. ``source_ref`` reads every path at that ref of the current
    repository; ``stdin`` reads the documents of ``-``.
    """

    known = set(ctx.catalog.kinds())
    pinned = GitSource.resolve(source_ref) if source_ref is not None else None

    def reader(path: Path) -> Iterable[Resource]:
        docs = _read_documents(path, pinned, stdin)
        for source, doc in docs:
            # Name the file: a directory apply reads every *.yml and *.yaml.
            if doc.kind not in known:
                raise ConfigError(
                    f"{source}: unknown kind {doc.kind!r} (available: {', '.join(sorted(known))})",
                    category="invalid",
                )
        return [with_default_organization(ctx, doc) for _source, doc in docs]

    engine = build_mutation_engine(ctx, allow_unverified=gate.allow_unverified)
    plan = prepare_apply_file(engine, reader, files, catalog=ctx.catalog, fk=ctx.fk)
    if not check:
        return run_mutation_plan(ctx, engine, plan, gate)
    previews = preview_and_execute(
        ctx, engine, plan, replace(gate, confirm=gate.confirm.as_dry_run())
    )
    return outcome_results(
        previews, predicate_hit=any(outcome.action != "unchanged" for outcome in previews)
    )
