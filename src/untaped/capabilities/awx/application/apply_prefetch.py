"""FK prefetch planning for multi-resource apply."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from untaped.capabilities.awx.application.apply_planner import scope_for
from untaped.capabilities.awx.application.ports import Catalog
from untaped.capabilities.awx.domain import Resource


def prefetch_plan(
    docs: Iterable[Resource], *, catalog: Catalog
) -> dict[str, list[dict[str, str] | None]]:
    """Derive the ``(kind, scope)`` groups the apply pass will look up.

    Walks every doc's payload, finds each ``FkRef``, and records the
    scope under which the lookup will happen (no scope = global). The
    result is fed to :meth:`FkResolver.prefetch` which collapses each
    group into one bulk list.
    """
    seen: dict[str, set[frozenset[tuple[str, str]]]] = defaultdict(set)
    for doc in docs:
        spec = catalog.get(doc.kind)
        body = doc.spec if isinstance(doc.spec, dict) else {}
        for ref in spec.fk_refs:
            if ref.polymorphic:
                continue
            if ref.kind is None or ref.field not in body or body[ref.field] is None:
                continue
            # Reuse apply-time scope resolution so prefetch warms the same
            # cache buckets the apply pass will hit. Body-only lookup misses
            # inventory-child refs (Group.hosts/children) where scope lives
            # on metadata.parent rather than in the body.
            scope = scope_for(ref, doc) or {}
            values = (
                body[ref.field]
                if ref.multi and isinstance(body[ref.field], list)
                else [body[ref.field]]
            )
            for value in values:
                if isinstance(value, dict):
                    scoped = {
                        key: item
                        for key, item in value.items()
                        if key not in {"kind", "name", "parent"} and isinstance(item, str)
                    }
                    seen[ref.kind].add(frozenset(scoped.items()))
                elif isinstance(value, str):
                    seen[ref.kind].add(frozenset(scope.items()))
    return {
        kind: [dict(items) if items else None for items in scopes] for kind, scopes in seen.items()
    }
