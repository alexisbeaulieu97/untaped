"""Compose declarative file preparation with the shared CLI mutation gate."""

from collections.abc import Iterable
from pathlib import Path

from untaped.api import ConfigError, OutputFormat, echo
from untaped.capabilities.awx.application import ApplyFile, ApplyResource
from untaped.capabilities.awx.cli._context import AwxContext
from untaped.capabilities.awx.cli._mutation_runner import run_mutation_plan
from untaped.capabilities.awx.domain import Resource
from untaped.capabilities.awx.infrastructure.yaml_io import read_resources


def build_apply_resource(ctx: AwxContext, *, allow_unverified: bool = False) -> ApplyResource:
    """Wire the authoritative application engine for all configuration commands."""
    return ApplyResource(
        client=ctx.repo,
        catalog=ctx.catalog,
        fk=ctx.fk,
        strategies=ctx.strategies,
        warn=lambda msg: echo(f"warning: {msg}", err=True),
        allow_unverified=allow_unverified,
    )


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

    def reader(path: Path) -> Iterable[Resource]:
        docs = list(read_resources(path))
        wrong = sorted({doc.kind for doc in docs if kind_filter and doc.kind != kind_filter})
        if wrong:
            raise ConfigError(f"expected only {kind_filter} documents; found {', '.join(wrong)}")
        return docs

    apply_one = build_apply_resource(ctx, allow_unverified=allow_unverified)
    plan = ApplyFile(apply_one, reader, ctx.catalog, ctx.fk).prepare(file)
    run_mutation_plan(
        ctx,
        apply_one.engine,
        plan,
        yes=yes,
        dry_run=dry_run,
        continue_on_error=continue_on_error,
        parallel=parallel,
        allow_unverified=allow_unverified,
        fmt=fmt,
        columns=columns,
    )
