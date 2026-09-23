"""Compose declarative file preparation with the shared CLI mutation gate."""

from collections.abc import Iterable
from pathlib import Path

from untaped.capabilities.awx.application import BatchMutationEngine, prepare_apply_file
from untaped.capabilities.awx.cli._context import AwxContext
from untaped.capabilities.awx.cli._mutation_runner import run_mutation_plan
from untaped.capabilities.awx.domain import Resource
from untaped.capabilities.awx.infrastructure.yaml_io import read_resource_files
from untaped.capability_api import ConfigError, OutputFormat, echo


def build_mutation_engine(
    ctx: AwxContext, *, allow_unverified: bool = False
) -> BatchMutationEngine:
    """Wire the authoritative application engine for all configuration commands."""
    return BatchMutationEngine(
        client=ctx.repo,
        catalog=ctx.catalog,
        fk=ctx.fk,
        strategies=ctx.strategies,
        warn=lambda msg: echo(f"warning: {msg}", err=True),
        allow_unverified=allow_unverified,
    )


def _with_default_organization(ctx: AwxContext, doc: Resource) -> Resource:
    """Scope org-less documents of org-scoped kinds by ``awx.default_organization``.

    Selection and ``awx test`` already scope this way; without it an apply
    would match a same-named resource in whichever organization held one.
    With no default, a name found in several organizations stays ambiguous.

    An explicit ``metadata.organization`` (a name, or ``null`` for an
    org-less record such as a global workflow template) is never filled, and
    a ``spec.organization`` name is the identity when metadata omits one.
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


def run_apply(
    ctx: AwxContext,
    file: Path,
    *,
    yes: bool,
    dry_run: bool = False,
    continue_on_error: bool = False,
    allow_unverified: bool = False,
    fmt: OutputFormat = "table",
    columns: list[str] | None = None,
    kind_filter: str | None = None,
    parallel: int = 1,
) -> None:
    """Prepare once, confirm once, execute the same complete batch."""

    known = set(ctx.catalog.kinds())

    def reader(path: Path) -> Iterable[Resource]:
        docs = list(read_resource_files(path))
        for source, doc in docs:
            # Name the file: a directory apply reads every *.yml and *.yaml.
            if doc.kind not in known:
                raise ConfigError(
                    f"{source}: unknown kind {doc.kind!r} (available: {', '.join(sorted(known))})"
                )
            if kind_filter and doc.kind != kind_filter:
                raise ConfigError(
                    f"{source}: expected only {kind_filter} documents; found {doc.kind}"
                )
        return [_with_default_organization(ctx, doc) for _source, doc in docs]

    engine = build_mutation_engine(ctx, allow_unverified=allow_unverified)
    plan = prepare_apply_file(engine, reader, file, catalog=ctx.catalog, fk=ctx.fk)
    run_mutation_plan(
        ctx,
        engine,
        plan,
        yes=yes,
        dry_run=dry_run,
        continue_on_error=continue_on_error,
        parallel=parallel,
        allow_unverified=allow_unverified,
        fmt=fmt,
        columns=columns,
    )
