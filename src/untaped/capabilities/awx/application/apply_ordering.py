"""Dependency ordering for multi-resource apply."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from untaped.capabilities.awx.application.ports import Catalog
from untaped.capabilities.awx.domain import Resource
from untaped.capabilities.awx.errors import AwxApiError


def topological_sort(docs: Iterable[Resource], *, catalog: Catalog) -> list[Resource]:
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

    # Resolve every kind once; raises AwxApiError on unknown kinds before
    # any sorting work, and gives every loop below O(1) spec access.
    specs = {kind: catalog.get(kind) for kind in kinds_in_docs}

    # Build a kind-level dependency graph restricted to kinds present in
    # the input. Cross-doc dependencies on kinds NOT present mean the
    # parent already exists in AWX — those don't constrain ordering.
    # Sub-endpoint multi-FKs are reconciled *after* the resource is
    # written (associate/disassociate POSTs against ``/<id>/<sub>/``), so
    # they don't constrain create order — and a self-reference like
    # ``Group.children → Group`` would otherwise spuriously trip the
    # cycle detector.
    edges: dict[str, set[str]] = defaultdict(set)
    for kind, spec in specs.items():
        for ref in spec.fk_refs:
            if ref.multi and ref.sub_endpoint is not None:
                continue
            if ref.kind is not None and ref.kind in kinds_in_docs and ref.kind != kind:
                edges[kind].add(ref.kind)

    # Polymorphic refs: read the referenced kind from each doc's data.
    for doc in docs_list:
        parent = doc.metadata.parent
        if parent is not None and parent.kind in kinds_in_docs:
            edges[doc.kind].add(parent.kind)
        for ref in specs[doc.kind].fk_refs:
            # ``metadata.parent`` (above) is the saved shape; a spec-level
            # ``{kind: ...}`` value names its kind the same way.
            value = doc.spec.get(ref.field) if ref.polymorphic and ref.kind_in_value else None
            referenced_kind = value.get(ref.kind_in_value) if isinstance(value, dict) else None
            if isinstance(referenced_kind, str) and referenced_kind in kinds_in_docs:
                edges[doc.kind].add(referenced_kind)

    # Tie-break ready kinds by the catalog's canonical order (Organization
    # before CredentialType, etc.). Falling back to the kind name keeps unknown-but-valid
    # kinds deterministic.
    kind_rank = {kind: i for i, kind in enumerate(catalog.kinds())}
    kind_order = _kahn_topological_order(kinds_in_docs, edges, rank=kind_rank)

    # Stable secondary ordering by metadata.name within a kind.
    rank = {kind: i for i, kind in enumerate(kind_order)}
    return sorted(docs_list, key=lambda d: (rank[d.kind], d.metadata.name))


def _kahn_topological_order(
    kinds: set[str],
    edges: dict[str, set[str]],
    *,
    rank: dict[str, int],
) -> list[str]:
    """Return ``kinds`` in dependency order: a kind appears after every
    other kind it depends on. Raises if there's a cycle.

    ``edges[k]`` is the set of kinds ``k`` depends on (must apply before).
    ``rank`` is the catalog's canonical ordering used as the tie-breaker
    when multiple kinds are ready at once.
    """

    def order_key(k: str) -> tuple[int, str]:
        return (rank.get(k, len(rank)), k)

    # In-degree = number of unresolved dependencies for each kind.
    in_degree = {k: len(edges.get(k, set())) for k in kinds}
    # Reverse edges: dependents[parent] = set of children.
    dependents: dict[str, set[str]] = defaultdict(set)
    for child, parents in edges.items():
        for parent in parents:
            dependents[parent].add(child)

    ready = sorted((k for k, deg in in_degree.items() if deg == 0), key=order_key)
    ordered: list[str] = []
    while ready:
        kind = ready.pop(0)
        ordered.append(kind)
        for child in sorted(dependents.get(kind, set()), key=order_key):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                ready.append(child)
        ready.sort(key=order_key)

    if len(ordered) != len(kinds):
        unresolved = sorted(k for k, deg in in_degree.items() if deg > 0)
        raise AwxApiError(f"cycle in apply order across kinds: {', '.join(unresolved)}")
    return ordered
