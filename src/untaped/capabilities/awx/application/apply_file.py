"""Read a complete document batch and prepare its fixed mutation plan once."""

from __future__ import annotations

from pathlib import Path

from untaped.capabilities.awx.application.apply_ordering import topological_sort
from untaped.capabilities.awx.application.apply_prefetch import prefetch_plan
from untaped.capabilities.awx.application.mutation_engine import BatchMutationEngine
from untaped.capabilities.awx.application.mutation_types import MutationPlan
from untaped.capabilities.awx.application.ports import Catalog, FkResolver, ResourceDocumentReader


def prepare_apply_file(
    engine: BatchMutationEngine,
    reader: ResourceDocumentReader,
    path: Path,
    *,
    catalog: Catalog,
    fk: FkResolver,
) -> MutationPlan:
    """Read, order, and validate the complete file batch once, before confirmation."""
    docs = topological_sort(list(reader(path)), catalog=catalog)
    prefetch = prefetch_plan(docs, catalog=catalog)
    if prefetch:
        fk.prefetch(prefetch)
    return engine.prepare(docs)
