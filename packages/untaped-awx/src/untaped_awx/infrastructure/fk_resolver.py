"""Translate between human names and AWX numeric IDs.

The resolver is process-local: a single CLI invocation never queries
the same ``(kind, name, scope)`` twice. Polymorphic FKs (Schedule's
``parent``) accept a typed dict and dispatch on ``kind``.

For scope-aware lookups we use AWX's ``__name`` related-field syntax
(e.g. ``?organization__name=Default``) so we don't need to recursively
resolve the scope itself.

For bulk apply / save flows, callers can warm the cache via
:meth:`prefetch` to collapse N per-name round trips into one paginated
``list`` per ``(kind, scope)`` group.

**Thread-safety.** ``ApplyFile._apply_kind`` dispatches phase-1 doc
applies across a ``ThreadPoolExecutor``; each worker may call
:meth:`name_to_id` / :meth:`id_to_name` concurrently. The two caches
(``_name_cache``, ``_id_cache``) are protected by a single
``threading.RLock`` taken across the read + repo call + write window
so a concurrent caller can't (a) race a check-then-set into duplicate
repo calls for the same key, or (b) trip ``RuntimeError: dictionary
changed size during iteration`` if another thread is iterating
elsewhere. ``RLock`` (not ``Lock``) keeps the future safe: any
cross-cache lookup that ends up calling back into this resolver
won't deadlock on itself.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from untaped_awx.errors import AwxApiError, ResourceNotFound
from untaped_awx.infrastructure.catalog import AwxResourceCatalog

if TYPE_CHECKING:
    from untaped_awx.application.ports import ResourceClient


class FkResolver:
    def __init__(self, repo: ResourceClient, catalog: AwxResourceCatalog) -> None:
        self._repo = repo
        self._catalog = catalog
        self._name_cache: dict[tuple[str, str, frozenset[tuple[str, str]]], int] = {}
        self._id_cache: dict[tuple[str, int], str] = {}
        # See module docstring "Thread-safety". Held across the entire
        # check-call-write window so two workers racing on the same
        # ``(kind, name, scope)`` collapse into one repo lookup.
        self._cache_lock = threading.RLock()

    def name_to_id(
        self,
        kind: str,
        name: str,
        *,
        scope: dict[str, str] | None = None,
    ) -> int:
        scope = scope or {}
        key = (kind, name, frozenset(scope.items()))
        with self._cache_lock:
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
        with self._cache_lock:
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

    def prefetch(self, plan: dict[str, list[dict[str, str] | None]]) -> None:
        """Warm the cache for one paginated ``list`` per ``(kind, scope)``.

        ``plan`` maps a kind to the scopes whose records the caller is
        about to look up by name or id. Each unique ``(kind, scope)``
        triggers one paginated ``list`` query against AWX; every
        returned record's ``name`` and ``id`` are cached. Subsequent
        calls to :meth:`name_to_id` or :meth:`id_to_name` for the same
        kind+scope hit the cache; misses fall through to the
        per-record lookup so a missing prefetch never breaks
        correctness — only performance.

        Errors during prefetch are intentionally not propagated: a
        flaky bulk fetch shouldn't fail an apply that the per-call
        path could still satisfy.
        """
        seen: set[tuple[str, frozenset[tuple[str, str]]]] = set()
        for kind, scopes in plan.items():
            for raw_scope in scopes:
                scope = raw_scope or {}
                key = (kind, frozenset(scope.items()))
                if key in seen:
                    continue
                seen.add(key)
                self._prefetch_one(kind, scope)

    def _prefetch_one(self, kind: str, scope: dict[str, str]) -> None:
        spec = self._catalog.get(kind)
        params: dict[str, str] = {f"{k}__name": v for k, v in scope.items()}
        cache_scope = frozenset(scope.items())
        try:
            with self._cache_lock:
                for record in self._repo.list(spec, params=params or None):
                    record_name = record.get("name")
                    if not isinstance(record_name, str):
                        continue
                    record_id = int(record["id"])
                    self._name_cache.setdefault((kind, record_name, cache_scope), record_id)
                    self._id_cache.setdefault((kind, record_id), record_name)
        except AwxApiError:
            # Per-record name_to_id calls remain authoritative; a flaky bulk
            # fetch silently degrades to per-call resolution rather than
            # failing an apply that the per-call path could still satisfy.
            return
