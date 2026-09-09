"""Translate between human names and AWX numeric IDs.

The resolver is process-local: a single CLI invocation never queries
the same ``(kind, name, scope)`` twice. Polymorphic FKs (Schedule's
``parent``) accept a typed dict and dispatch on ``kind``.

For scope-aware lookups we use AWX's ``__name`` related-field syntax
(e.g. ``?organization__name=Default``) so we don't need to recursively
resolve the scope itself.

For bulk apply / save flows, callers can warm the cache via
:meth:`prefetch` to collapse N per-name round trips into one paginated
``list`` per ``(kind, scope)`` group. Prefetch failures call the
injected ``warn`` hook and fall back to per-record lookups so the
degradation is visible without breaking the apply.

Thread-safety: the two caches are guarded by ``self._cache_lock``
across the read+repo-call+write window so concurrent workers racing
on the same ``(kind, name, scope)`` cache miss collapse into one repo
lookup. ``Lock`` (not ``RLock``) so a future cross-cache call back
into the resolver surfaces as a deadlock rather than silent recursion.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from untaped.capabilities.awx.domain import IdentityRef
from untaped.capabilities.awx.errors import (
    AmbiguousIdentityError,
    AwxApiError,
    BadRequest,
    ResourceNotFound,
)
from untaped.capabilities.awx.infrastructure.catalog import AwxResourceCatalog
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec

if TYPE_CHECKING:
    from untaped.capabilities.awx.application.ports import ResourceClient

WarnFn = Callable[[str], None]


def _noop_warn(_msg: str) -> None: ...


class FkResolver:
    def __init__(
        self,
        repo: ResourceClient,
        catalog: AwxResourceCatalog,
        *,
        warn: WarnFn = _noop_warn,
    ) -> None:
        self._repo = repo
        self._catalog = catalog
        self._warn = warn
        self._name_cache: dict[tuple[str, str, frozenset[tuple[str, str]]], int] = {}
        self._ambiguous_names: dict[tuple[str, str, frozenset[tuple[str, str]]], set[int]] = {}
        self._id_cache: dict[tuple[str, int], str] = {}
        self._cache_lock = threading.Lock()

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
            if key in self._ambiguous_names:
                raise AmbiguousIdentityError(
                    kind, {"name": name, **scope}, match_count=len(self._ambiguous_names[key])
                )
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

    def validate_id(self, kind: str, id_: int, *, scope: dict[str, str] | None = None) -> int:
        if isinstance(id_, bool) or not isinstance(id_, int) or id_ <= 0:
            raise BadRequest(f"foreign key {kind} requires a positive integer ID")
        spec = self._catalog.get(kind)
        try:
            record = self._repo.get(spec, id_)
        except KeyError as exc:
            raise ResourceNotFound(kind, {"id": id_}) from exc
        if record.get("id") != id_:
            raise ResourceNotFound(kind, {"id": id_})
        if scope:
            # ID remains the selector. Related-name filters only constrain its
            # scope; an ambiguous display label never changes this target.
            scoped_record = self._repo.find(
                spec,
                params={"id": str(id_), **{f"{key}__name": value for key, value in scope.items()}},
            )
            if scoped_record is None or scoped_record.get("id") != id_:
                raise ResourceNotFound(kind, {"id": id_, **scope})
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

    def id_to_identity(self, kind: str, id_: int) -> IdentityRef:
        if kind == "UnifiedJobTemplate":
            lookup = AwxResourceSpec(
                kind=kind,
                cli_name="",
                api_path="unified_job_templates",
                identity_keys=("name",),
                canonical_fields=(),
            )
            record = self._repo.get(lookup, id_)
            kinds = {
                "job_template": "JobTemplate",
                "workflow_job_template": "WorkflowJobTemplate",
                "project": "Project",
                "inventory_source": "InventorySource",
            }
            resolved_kind = kinds.get(str(record.get("type", "")))
            if resolved_kind is None:
                raise BadRequest("unsupported schedule parent type")
            return self.id_to_identity(resolved_kind, id_)
        spec = self._catalog.get(kind)
        record = self._repo.get(spec, id_)
        parent = None
        organization = None
        if spec.apply_strategy == "inventory_child":
            inventory_id = record.get("inventory")
            if not isinstance(inventory_id, int):
                raise BadRequest(f"{kind}#{id_} is missing inventory ancestry")
            parent = self.id_to_identity("Inventory", inventory_id)
        elif "organization" in spec.identity_keys:
            org_id = record.get("organization")
            if isinstance(org_id, int):
                organization = self.id_to_name("Organization", org_id)
        return IdentityRef(
            kind=kind, name=str(record["name"]), organization=organization, parent=parent
        )

    def resolve_polymorphic(self, value: dict[str, Any]) -> tuple[str, int]:
        """Resolve ``{"kind": ..., "name": ..., "organization": ...}`` to ``(kind, id)``."""
        reference = IdentityRef.model_validate(value)
        try:
            scope = reference.lookup_scope()
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc
        return reference.kind, self.name_to_id(reference.kind, reference.name, scope=scope)

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
        # Drain the paginated iterator off-lock so a `prefetch` running
        # while workers are live doesn't block their `name_to_id` calls
        # for the duration of the network walk. Per-record `setdefault`
        # writes are serialised by the lock; the listing is the only
        # part with non-trivial I/O.
        try:
            records = list(self._repo.list(spec, params=params or None))
        except AwxApiError as exc:
            # Per-record name_to_id calls remain authoritative: a flaky bulk
            # fetch falls back to per-call resolution rather than failing an
            # apply that the per-call path could still satisfy. Warn so the
            # user sees why the apply just slowed down.
            scope_str = ", ".join(f"{k}={v}" for k, v in sorted(scope.items()))
            target = f"{kind} ({scope_str})" if scope_str else kind
            self._warn(
                f"FK prefetch for {target} failed ({exc}); falling back to per-record lookups"
            )
            return
        with self._cache_lock:
            for record in records:
                record_name = record.get("name")
                if not isinstance(record_name, str):
                    continue
                record_id = int(record["id"])
                name_key = (kind, record_name, cache_scope)
                previous = self._name_cache.get(name_key)
                if name_key in self._ambiguous_names:
                    self._ambiguous_names[name_key].add(record_id)
                elif previous is not None and previous != record_id:
                    self._ambiguous_names[name_key] = {previous, record_id}
                    del self._name_cache[name_key]
                else:
                    self._name_cache[name_key] = record_id
                self._id_cache.setdefault((kind, record_id), record_name)
