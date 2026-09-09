"""Read a complete document batch, prepare once, then execute its fixed plan."""

from __future__ import annotations

from pathlib import Path

from untaped.capabilities.awx.application.apply_ordering import topological_sort
from untaped.capabilities.awx.application.apply_prefetch import prefetch_plan
from untaped.capabilities.awx.application.apply_resource import ApplyResource
from untaped.capabilities.awx.application.mutation_types import MutationPlan
from untaped.capabilities.awx.application.ports import Catalog, FkResolver, ResourceDocumentReader
from untaped.capabilities.awx.domain import ApplyOutcome

APPLY_PARALLEL_CAP = 10


class ApplyFile:
    def __init__(
        self,
        apply_one: ApplyResource,
        reader: ResourceDocumentReader,
        catalog: Catalog,
        fk: FkResolver,
        *,
        parallel: int = 1,
    ) -> None:
        if parallel < 1:
            raise ValueError(f"parallel must be >= 1, got {parallel}")
        self._engine = apply_one.engine
        self._reader = reader
        self._catalog = catalog
        self._fk = fk
        self._parallel = min(parallel, APPLY_PARALLEL_CAP)

    def __call__(
        self,
        path: Path,
        *,
        write: bool = False,
        continue_on_error: bool = False,
    ) -> list[ApplyOutcome]:
        plan = self.prepare(path)
        if not write:
            return [operation.preview for operation in plan.operations]
        return self._engine.execute(
            plan,
            parallel=self._parallel,
            continue_on_error=continue_on_error,
        ).outcomes

    def prepare(self, path: Path) -> MutationPlan:
        """Read and validate the complete file batch once, before confirmation."""
        docs = topological_sort(list(self._reader(path)), catalog=self._catalog)
        prefetch = prefetch_plan(docs, catalog=self._catalog)
        if prefetch:
            self._fk.prefetch(prefetch)
        return self._engine.prepare(docs)
