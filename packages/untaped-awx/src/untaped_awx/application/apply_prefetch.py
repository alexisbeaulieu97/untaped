"""FK prefetch planning for multi-resource apply."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from untaped_awx.application.ports import Catalog
from untaped_awx.application.references import references_of
from untaped_awx.domain import Resource


def prefetch_plan(
    docs: Iterable[Resource], *, catalog: Catalog
) -> dict[str, list[dict[str, str] | None]]:
    """Derive the ``(kind, scope)`` groups the apply pass will look up.

    Walks every doc's foreign keys (:func:`references_of`, polymorphic
    parents and workflow nodes left out) and records the scope under which
    each lookup by name will happen (no scope = global). The result is fed to
    :meth:`FkResolver.prefetch` which collapses each group into one bulk list.
    """
    seen: dict[str, set[frozenset[tuple[str, str]]]] = defaultdict(set)
    for doc in docs:
        spec = catalog.get(doc.kind)
        fk_fields = {ref.field for ref in spec.fk_refs if not ref.polymorphic}
        for site in references_of(spec, doc):
            if site.field not in fk_fields:
                continue
            if isinstance(site.value, dict):
                scoped = {
                    key: item
                    for key, item in site.value.items()
                    if key not in {"kind", "name", "parent"} and isinstance(item, str)
                }
                seen[site.kind].add(frozenset(scoped.items()))
            elif isinstance(site.value, str):
                # The apply-time scope (``scope_for``), so prefetch warms the
                # cache buckets the apply pass will hit, including
                # inventory-child refs whose scope lives on metadata.parent.
                seen[site.kind].add(frozenset((site.scope or {}).items()))
    return {
        kind: [dict(items) if items else None for items in scopes] for kind, scopes in seen.items()
    }
