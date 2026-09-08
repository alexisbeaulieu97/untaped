"""AWX-owned selection resolution for reads and mutations.

Selection is intentionally separate from mutation planning.  It resolves
names, explicit IDs, typed v1 pipe envelopes, or server filters into concrete
records once; callers pass the resulting IDs into a mutation plan and never
re-resolve a name after preview or confirmation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from untaped.capabilities.awx.application.ports import Catalog, ResourceClient
from untaped.capabilities.awx.domain import ResourceSpec
from untaped.capabilities.awx.errors import BadRequest, ResourceNotFound
from untaped.capability_api import ConfigError, PipeEnvelope

_CAMEL_TAIL = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_RUN = re.compile(r"([a-z0-9])([A-Z])")


@dataclass(frozen=True)
class SelectionRequest:
    """One mutually-exclusive selection source.

    ``names`` and ``ids`` are deliberately separate so numeric resource names
    remain usable.  ``pipe`` is already parsed with the shared v1 parser; the
    resolver validates its producer kind and numeric IDs before fetching.
    """

    names: tuple[str, ...] = ()
    ids: tuple[str, ...] = ()
    pipe: tuple[PipeEnvelope, ...] | None = None
    filters: Mapping[str, str] = field(default_factory=dict)
    search: str | None = None
    scope: Mapping[str, str] = field(default_factory=dict)
    all: bool = False
    by_id: bool = False
    mutation: bool = False


@dataclass(frozen=True)
class SelectedResource:
    """A selected server record with its fixed numeric identity."""

    kind: str
    id: int
    name: str | None
    scope: dict[str, str]
    record: dict[str, Any]


class SelectionResolver:
    """Resolve one selection request without making mutation decisions."""

    def __init__(self, client: ResourceClient, catalog: Catalog) -> None:
        self._client = client
        self._catalog = catalog

    def resolve(
        self,
        spec: ResourceSpec,
        request: SelectionRequest | None = None,
    ) -> tuple[SelectedResource, ...]:
        """Resolve exactly one source and deduplicate by ``kind + id``."""
        if request is None:
            request = SelectionRequest()

        sources = (
            int(bool(request.names))
            + int(bool(request.ids))
            + int(request.pipe is not None)
            + int(bool(request.filters) or request.search is not None)
            + int(request.all)
        )
        if sources > 1:
            raise ConfigError(
                "selection sources are exclusive: use names, --by-id, --stdin, filters/search, "
                "or --all"
            )
        if request.mutation and sources == 0:
            raise ConfigError("mutation requires an explicit selection or --all")
        if request.by_id and not request.ids:
            raise ConfigError("--by-id requires explicit IDs")
        if request.names and request.by_id:
            raise ConfigError("--by-id applies to IDs, not names")
        if request.ids and not request.by_id:
            raise ConfigError("IDs require --by-id")

        effective_scope = {_scope_path(spec, key): value for key, value in request.scope.items()}
        if request.pipe is not None:
            return self._from_pipe(spec, request.pipe, effective_scope)
        if request.names:
            return self._from_names(spec, request.names, effective_scope)
        if request.ids:
            return self._from_ids(spec, request.ids, effective_scope)
        if request.filters or request.search is not None or request.all:
            return self._from_query(
                spec,
                filters=dict(request.filters),
                search=request.search,
                scope=effective_scope,
            )
        return ()

    def _from_pipe(
        self,
        spec: ResourceSpec,
        envelopes: tuple[PipeEnvelope, ...],
        scope: dict[str, str],
    ) -> tuple[SelectedResource, ...]:
        expected_kind = _pipe_kind(spec.kind)
        selected: list[SelectedResource] = []
        for envelope in envelopes:
            if envelope.kind != expected_kind:
                raise ConfigError(
                    f"pipe record line {envelope.lineno} has kind {envelope.kind!r}; "
                    f"expected {expected_kind!r}"
                )
            id_ = envelope.record.get("id")
            if not isinstance(id_, int) or isinstance(id_, bool) or id_ <= 0:
                raise ConfigError(
                    f"line {envelope.lineno}: pipe record requires a positive integer id"
                )
            record = _record_dict(self._client.get(spec, id_))
            validate_scope(spec, record, scope, client=self._client, catalog=self._catalog)
            selected.append(_selected(spec, record, scope))
        return _dedupe(selected)

    def _from_names(
        self,
        spec: ResourceSpec,
        names: tuple[str, ...],
        scope: dict[str, str],
    ) -> tuple[SelectedResource, ...]:
        selected: list[SelectedResource] = []
        for name in names:
            record = self._client.find_by_identity(spec, name=name, scope=scope or None)
            if record is None:
                raise ResourceNotFound(spec.kind, {"name": name, **scope})
            values = _record_dict(record)
            validate_scope(spec, values, scope, client=self._client, catalog=self._catalog)
            selected.append(_selected(spec, values, scope))
        return _dedupe(selected)

    def _from_ids(
        self,
        spec: ResourceSpec,
        ids: tuple[str, ...],
        scope: dict[str, str],
    ) -> tuple[SelectedResource, ...]:
        selected: list[SelectedResource] = []
        for raw_id in ids:
            id_ = _positive_id(raw_id)
            record = _record_dict(self._client.get(spec, id_))
            validate_scope(spec, record, scope, client=self._client, catalog=self._catalog)
            selected.append(_selected(spec, record, scope))
        return _dedupe(selected)

    def _from_query(
        self,
        spec: ResourceSpec,
        *,
        filters: dict[str, str],
        search: str | None,
        scope: dict[str, str],
    ) -> tuple[SelectedResource, ...]:
        params = dict(filters)
        if search is not None:
            params["search"] = search
        for key, value in scope.items():
            scoped_key = f"{key}__name"
            if scoped_key in params and params[scoped_key] != value:
                raise ConfigError(f"selection scope conflicts with filter {key!r}")
            params[scoped_key] = value
        selected: list[SelectedResource] = []
        for record in self._client.list(spec, params=params or None):
            values = _record_dict(record)
            validate_scope(spec, values, scope, client=self._client, catalog=self._catalog)
            selected.append(_selected(spec, values, scope))
        return _dedupe(selected)


def _positive_id(raw_id: str) -> int:
    if not isinstance(raw_id, str):
        raise ConfigError(f"not a numeric id: {raw_id!r}")
    try:
        id_ = int(raw_id)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"not a numeric id: {raw_id!r}") from exc
    if id_ <= 0:
        raise ConfigError(f"resource id must be positive: {raw_id!r}")
    return id_


def _record_dict(record: Any) -> dict[str, Any]:
    if hasattr(record, "model_dump"):
        return dict(record.model_dump())
    return dict(record)


def _selected(
    spec: ResourceSpec,
    record: dict[str, Any],
    scope: Mapping[str, str],
) -> SelectedResource:
    id_ = record.get("id")
    if not isinstance(id_, int) or isinstance(id_, bool) or id_ <= 0:
        raise BadRequest(f"{spec.kind} selection returned an invalid id")
    name = record.get("name")
    return SelectedResource(
        kind=spec.kind,
        id=id_,
        name=name if isinstance(name, str) else None,
        scope=dict(scope),
        record=record,
    )


def _dedupe(selected: list[SelectedResource]) -> tuple[SelectedResource, ...]:
    seen: set[tuple[str, int]] = set()
    result: list[SelectedResource] = []
    for item in selected:
        key = (item.kind, item.id)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return tuple(result)


def validate_scope(
    spec: ResourceSpec,
    record: Mapping[str, Any],
    scope: Mapping[str, str],
    *,
    client: ResourceClient,
    catalog: Catalog,
) -> None:
    """Prove requested names against summary fields or referenced numeric IDs."""
    for path, expected in scope.items():
        current = record
        parts = _scope_path(spec, path).split("__")
        for index, relationship in enumerate(parts):
            summary = current.get("summary_fields") or {}
            summary_record = summary.get(relationship) if isinstance(summary, Mapping) else None
            if index == len(parts) - 1 and isinstance(summary_record, Mapping):
                name = summary_record.get("name")
                if name is not None:
                    if name != expected:
                        raise ResourceNotFound(spec.kind, {"id": record.get("id"), path: expected})
                    break
            referenced = _scope_reference(current, relationship, client=client, catalog=catalog)
            if referenced is None or (
                index == len(parts) - 1 and referenced.get("name") != expected
            ):
                raise ResourceNotFound(spec.kind, {"id": record.get("id"), path: expected})
            current = referenced


def _pipe_kind(kind: str) -> str:
    snake = _CAMEL_RUN.sub(r"\1_\2", _CAMEL_TAIL.sub(r"\1_\2", kind)).lower()
    return f"awx.{snake}"


__all__ = ["SelectedResource", "SelectionRequest", "SelectionResolver"]


def _scope_path(spec: ResourceSpec, path: str) -> str:
    first, separator, tail = path.partition("__")
    if first == "parent":
        if spec.apply_strategy == "inventory_child":
            first = "inventory"
        elif spec.apply_strategy == "schedule":
            first = "unified_job_template"
        else:
            raise BadRequest(f"{spec.kind} does not have a parent scope")
    return first + (separator + tail if separator else "")


def _scope_reference(
    record: Mapping[str, Any], relationship: str, *, client: ResourceClient, catalog: Catalog
) -> Mapping[str, Any] | None:
    summaries = record.get("summary_fields") or {}
    summary = summaries.get(relationship) if isinstance(summaries, Mapping) else None
    value = record.get(relationship)
    if isinstance(value, Mapping):
        return value
    if value is None and isinstance(summary, Mapping):
        value = summary.get("id")
    if not isinstance(value, int) or isinstance(value, bool):
        return summary if isinstance(summary, Mapping) else None
    kind = {"organization": "Organization", "inventory": "Inventory", "project": "Project"}.get(
        relationship
    )
    if kind is None and relationship == "unified_job_template" and isinstance(summary, Mapping):
        raw_kind = summary.get("unified_job_type") or summary.get("type")
        if isinstance(raw_kind, str):
            kind = next(
                (
                    item
                    for item in catalog.kinds()
                    if item.casefold() == raw_kind.replace("_", "").casefold()
                ),
                None,
            )
    if kind is None:
        raise BadRequest(f"unsupported scope relationship {relationship!r}")
    return _record_dict(client.get(catalog.get(kind), value))
