"""Translate between human names and AWX numeric IDs.

The resolver is process-local: a single CLI invocation never queries
the same ``(kind, name, scope)`` twice. Polymorphic FKs (Schedule's
``parent``) accept a typed dict and dispatch on ``kind``.

For scope-aware lookups we use AWX's ``__name`` related-field syntax
(e.g. ``?organization__name=Default``) so we don't need to recursively
resolve the scope itself.
"""

from __future__ import annotations

from typing import Any

from untaped_awx.errors import ResourceNotFound
from untaped_awx.infrastructure.catalog import AwxResourceCatalog
from untaped_awx.infrastructure.resource_repo import ResourceRepository


class FkResolver:
    def __init__(self, repo: ResourceRepository, catalog: AwxResourceCatalog) -> None:
        self._repo = repo
        self._catalog = catalog
        self._name_cache: dict[tuple[str, str, frozenset[tuple[str, str]]], int] = {}
        self._id_cache: dict[tuple[str, int], str] = {}

    def name_to_id(
        self,
        kind: str,
        name: str,
        *,
        scope: dict[str, str] | None = None,
    ) -> int:
        scope = scope or {}
        key = (kind, name, frozenset(scope.items()))
        if key in self._name_cache:
            return self._name_cache[key]
        spec = self._catalog.get(kind)
        record = self._repo.find_by_identity(spec, name=name, scope=scope)
        if record is None:
            raise ResourceNotFound(kind, {"name": name, **scope})
        id_ = int(record["id"])
        self._name_cache[key] = id_
        self._id_cache[(kind, id_)] = name
        return id_

    def id_to_name(self, kind: str, id_: int) -> str:
        cache_key = (kind, id_)
        if cache_key in self._id_cache:
            return self._id_cache[cache_key]
        spec = self._catalog.get(kind)
        record = self._repo.get(spec, id_)
        name = str(record.get("name", ""))
        self._id_cache[cache_key] = name
        return name

    def resolve_polymorphic(self, value: dict[str, Any]) -> tuple[str, int]:
        """Resolve ``{"kind": ..., "name": ..., "organization": ...}`` to ``(kind, id)``."""
        kind = value["kind"]
        name = value["name"]
        scope = {k: v for k, v in value.items() if k not in {"kind", "name"} and v is not None}
        return kind, self.name_to_id(kind, name, scope=scope)
