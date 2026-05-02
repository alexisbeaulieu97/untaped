"""Use case: apply a file or directory of resource docs in dependency order.

The orchestrator collects every doc via an injected
:class:`ResourceDocumentReader`, derives a kind dependency graph from
each spec's ``fk_refs`` (consulting the :class:`Catalog`), topologically
sorts the docs so an upsert can resolve its FKs against already-applied
parents, then dispatches each through :class:`ApplyResource`. Errors are
non-fatal by default; pass ``fail_fast=True`` to abort on first failure.

Note: the reader is a port (Protocol) defined in
``application/ports``. Concrete YAML / JSON / stdin readers live in
infrastructure and are wired by the CLI composition root.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from untaped_awx.application.apply_resource import ApplyResource
from untaped_awx.application.ports import Catalog, ResourceDocumentReader
from untaped_awx.domain import ApplyOutcome, Resource
from untaped_awx.errors import AwxApiError


class ApplyFile:
    def __init__(
        self,
        apply_one: ApplyResource,
        reader: ResourceDocumentReader,
        catalog: Catalog,
    ) -> None:
        self._apply_one = apply_one
        self._reader = reader
        self._catalog = catalog

    def __call__(
        self,
        path: Path,
        *,
        write: bool = False,
        fail_fast: bool = False,
    ) -> list[ApplyOutcome]:
        docs = list(self._reader(path))
        ordered = _topological_sort(docs, catalog=self._catalog)
        outcomes: list[ApplyOutcome] = []
        for doc in ordered:
            try:
                outcomes.append(self._apply_one(doc, write=write))
            except AwxApiError as exc:
                outcomes.append(
                    ApplyOutcome(
                        kind=doc.kind,
                        name=doc.metadata.name,
                        action="failed",
                        detail=str(exc),
                    )
                )
                if fail_fast:
                    break
        return outcomes


def _topological_sort(docs: Iterable[Resource], *, catalog: Catalog) -> list[Resource]:
    """Order ``docs`` so every doc applies after the kinds it depends on.

    Edges come from each kind's ``ResourceSpec.fk_refs``: a non-polymorphic
    ``FkRef`` with a fixed ``kind`` declares "this kind references that
    kind". Polymorphic refs (e.g. Schedule's ``parent``) read the
    referenced kind from the resource's own data when available.

    Cycles raise :class:`AwxApiError` (a real cycle in AWX would mean a
    resource depends on itself). Unknown kinds also raise.
    """
    docs_list = list(docs)
    if not docs_list:
        return []

    kinds_in_docs = {d.kind for d in docs_list}

    # Validate kinds against the catalog before any sorting work — gives a
    # clear error rather than silently leaving unknown kinds last.
    for kind in kinds_in_docs:
        catalog.get(kind)  # raises AwxApiError on unknown

    # Build a kind-level dependency graph restricted to kinds present in
    # the input. Cross-doc dependencies on kinds NOT present mean the
    # parent already exists in AWX — those don't constrain ordering.
    edges: dict[str, set[str]] = defaultdict(set)
    for kind in kinds_in_docs:
        spec = catalog.get(kind)
        for ref in spec.fk_refs:
            if ref.kind is not None and ref.kind in kinds_in_docs:
                edges[kind].add(ref.kind)

    # Polymorphic refs: read the referenced kind from each doc's data.
    for doc in docs_list:
        spec = catalog.get(doc.kind)
        for ref in spec.fk_refs:
            if not ref.polymorphic or ref.kind_in_value is None:
                continue
            value = doc.spec.get(ref.field) if isinstance(doc.spec, dict) else None
            if value is None:
                value = _meta_value(doc, ref.field)
            referenced_kind = _extract_field(value, ref.kind_in_value)
            if isinstance(referenced_kind, str) and referenced_kind in kinds_in_docs:
                edges[doc.kind].add(referenced_kind)

    kind_order = _kahn_topological_order(kinds_in_docs, edges)

    # Stable secondary ordering by metadata.name within a kind.
    rank = {kind: i for i, kind in enumerate(kind_order)}
    return sorted(docs_list, key=lambda d: (rank[d.kind], d.metadata.name))


def _kahn_topological_order(kinds: set[str], edges: dict[str, set[str]]) -> list[str]:
    """Return ``kinds`` in dependency order: a kind appears after every
    other kind it depends on. Raises if there's a cycle.

    ``edges[k]`` is the set of kinds ``k`` depends on (must apply before).
    """
    # In-degree = number of unresolved dependencies for each kind.
    in_degree = {k: len(edges.get(k, set())) for k in kinds}
    # Reverse edges: dependents[parent] = set of children.
    dependents: dict[str, set[str]] = defaultdict(set)
    for child, parents in edges.items():
        for parent in parents:
            dependents[parent].add(child)

    # Pop kinds with zero remaining dependencies. Sort the ready set so the
    # output is deterministic across runs.
    ready = sorted(k for k, deg in in_degree.items() if deg == 0)
    ordered: list[str] = []
    while ready:
        kind = ready.pop(0)
        ordered.append(kind)
        for child in sorted(dependents.get(kind, set())):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                ready.append(child)
        ready.sort()

    if len(ordered) != len(kinds):
        unresolved = sorted(k for k, deg in in_degree.items() if deg > 0)
        raise AwxApiError(f"cycle in apply order across kinds: {', '.join(unresolved)}")
    return ordered


def _meta_value(doc: Resource, field: str) -> object:
    """Polymorphic refs (Schedule's ``parent``) live under ``metadata`` in
    the saved envelope, not ``spec``. Look there as a fallback so the
    sorter sees the dependency the reader already validated."""
    metadata = getattr(doc, "metadata", None)
    if metadata is None:
        return None
    return getattr(metadata, field, None)


def _extract_field(container: object, key: str) -> object:
    """Read ``key`` from a dict OR a pydantic model.

    Schedule's ``parent`` deserializes into an :class:`IdentityRef` (a
    pydantic model), not a plain dict, so a `value.get(...)` lookup
    silently returns nothing. Treat both shapes uniformly here.
    """
    if container is None:
        return None
    if isinstance(container, dict):
        return container.get(key)
    return getattr(container, key, None)
