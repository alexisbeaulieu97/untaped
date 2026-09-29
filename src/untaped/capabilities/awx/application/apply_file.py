"""Read a complete document batch and prepare its fixed mutation plan once."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from untaped.capabilities.awx.application.apply_ordering import topological_sort
from untaped.capabilities.awx.application.apply_prefetch import prefetch_plan
from untaped.capabilities.awx.application.mutation_engine import BatchMutationEngine
from untaped.capabilities.awx.application.mutation_types import MutationPlan
from untaped.capabilities.awx.application.ports import Catalog, FkResolver, ResourceDocumentReader


def prepare_apply_file(
    engine: BatchMutationEngine,
    reader: ResourceDocumentReader,
    paths: Iterable[Path],
    *,
    catalog: Catalog,
    fk: FkResolver,
) -> MutationPlan:
    """Read every path, then order and validate the whole batch once, before confirmation."""
    docs = topological_sort([doc for path in paths for doc in reader(path)], catalog=catalog)
    prefetch = prefetch_plan(docs, catalog=catalog)
    if prefetch:
        fk.prefetch(prefetch)
    return engine.prepare(docs)
