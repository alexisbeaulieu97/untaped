"""Read a complete document batch and prepare its fixed mutation plan once."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from untaped_awx.application.apply_ordering import topological_sort
from untaped_awx.application.apply_prefetch import prefetch_plan
from untaped_awx.application.mutation_engine import BatchMutationEngine
from untaped_awx.application.mutation_types import MutationPlan
from untaped_awx.application.ports import Catalog, FkResolver, ResourceDocumentReader
from untaped_awx.domain import Resource


def prepare_apply_file(
    engine: BatchMutationEngine,
    reader: ResourceDocumentReader,
    paths: Iterable[Path],
    *,
    catalog: Catalog,
    fk: FkResolver,
) -> MutationPlan:
    """Read every path, then order and validate the whole batch once, before confirmation."""
    docs = [doc for path in paths for doc in reader(path)]
    return prepare_documents(engine, docs, catalog=catalog, fk=fk)


def prepare_documents(
    engine: BatchMutationEngine,
    docs: Iterable[Resource],
    *,
    catalog: Catalog,
    fk: FkResolver,
) -> MutationPlan:
    """Order ``docs`` by their references, prefetch their names, and prepare them once."""
    ordered = topological_sort(docs, catalog=catalog)
    prefetch = prefetch_plan(ordered, catalog=catalog)
    if prefetch:
        fk.prefetch(prefetch)
    return engine.prepare(ordered)
