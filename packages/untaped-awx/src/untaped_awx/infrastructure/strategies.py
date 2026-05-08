"""Concrete :class:`ApplyStrategy` implementations.

The default strategy works for any kind whose write path is plain CRUD
against ``<api_path>/``. Schedule has its own strategy because creates
must POST against the parent's nested ``/schedules/`` endpoint.

Strategies bridge the dict-shaped payloads produced by application use
cases to the typed :class:`ResourceClient` boundary: dicts are wrapped
in :class:`WritePayload` on the way out, and :class:`ServerRecord`
results from the client are flattened to dict for the apply pipeline's
in-place strip / diff / preserve passes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from untaped_awx.domain import ResourceSpec, WritePayload
from untaped_awx.errors import AmbiguousIdentityError, BadRequest
from untaped_awx.infrastructure.spec import awx_api_path

if TYPE_CHECKING:
    from untaped_awx.application.ports import FkResolver, RawHttpResourceClient


class DefaultApplyStrategy:
    """Plain CRUD against ``<api_path>/``.

    Uses AWX's ``<scope_field>__name=<value>`` syntax to find existing
    resources by their identity (so we don't have to pre-resolve scope
    IDs just to look up).
    """

    def find_existing(
        self,
        spec: ResourceSpec,
        identity: dict[str, Any],
        *,
        client: RawHttpResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any] | None:
        params: dict[str, str] = {}
        for key, value in identity.items():
            if value is None:
                continue
            if key == "name":
                params["name"] = str(value)
            else:
                params[f"{key}__name"] = str(value)
        record = client.find(spec, params=params)
        return record.model_dump() if record is not None else None

    def create(
        self,
        spec: ResourceSpec,
        payload: dict[str, Any],
        identity: dict[str, Any],
        *,
        client: RawHttpResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any]:
        record = client.create(spec, WritePayload(**payload))
        return record.model_dump()

    def update(
        self,
        spec: ResourceSpec,
        existing: dict[str, Any],
        payload: dict[str, Any],
        *,
        client: RawHttpResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any]:
        record = client.update(spec, existing["id"], WritePayload(**payload))
        return record.model_dump()


class ScheduleApplyStrategy(DefaultApplyStrategy):
    """Schedule writes go through the parent's nested endpoint on create.

    AWX requires schedule creates at ``/<parent_path>/<parent_id>/schedules/``;
    updates go through the global ``/schedules/<id>/`` and so reuse
    :meth:`DefaultApplyStrategy.update`. Identity is ``(name, parent)``
    where ``parent`` is the polymorphic IdentityRef from
    ``resource.metadata.parent``.
    """

    _PARENT_PATHS: ClassVar[dict[str, str]] = {
        "JobTemplate": "job_templates",
        "WorkflowJobTemplate": "workflow_job_templates",
        "Project": "projects",
        "InventorySource": "inventory_sources",
    }

    def find_existing(
        self,
        spec: ResourceSpec,
        identity: dict[str, Any],
        *,
        client: RawHttpResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any] | None:
        parent = identity.get("parent")
        if parent is None:
            raise BadRequest("schedule identity missing 'parent'")
        parent_kind, parent_id = fk.resolve_polymorphic(_as_dict(parent))
        path = self._parent_path(parent_kind)
        return _find_unique(
            client,
            path=f"{path}/{parent_id}/schedules/",
            name=str(identity["name"]),
            kind="Schedule",
            ambiguity_label={"parent": f"{parent_kind}#{parent_id}"},
        )

    def create(
        self,
        spec: ResourceSpec,
        payload: dict[str, Any],
        identity: dict[str, Any],
        *,
        client: RawHttpResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any]:
        parent = identity.get("parent")
        if parent is None:
            raise BadRequest("schedule identity missing 'parent' for create")
        parent_kind, parent_id = fk.resolve_polymorphic(_as_dict(parent))
        path = self._parent_path(parent_kind)
        return client.request(
            "POST",
            f"{path}/{parent_id}/schedules/",
            json={"name": identity["name"], **payload},
        )

    @classmethod
    def _parent_path(cls, parent_kind: str) -> str:
        try:
            return cls._PARENT_PATHS[parent_kind]
        except KeyError as exc:
            raise BadRequest(
                f"schedule parent kind {parent_kind!r} not supported "
                f"(use one of {sorted(cls._PARENT_PATHS)})"
            ) from exc


class InventoryChildApplyStrategy(DefaultApplyStrategy):
    """Write path for resources whose parent is an Inventory.

    Used by :data:`HOST_SPEC` and :data:`GROUP_SPEC`. AWX accepts creates
    against either the global ``/<api_path>/`` (with ``inventory: <id>``
    in the body) or the nested ``/inventories/<id>/<api_path>/`` (which
    auto-fills the FK). We use the nested form so the user's spec body
    never carries ``inventory`` — keeping the body free of a redundant
    FK that's already implied by ``metadata.parent``. Updates reuse
    :meth:`DefaultApplyStrategy.update` (the global ``/<api_path>/<id>/``
    endpoint).
    """

    def find_existing(
        self,
        spec: ResourceSpec,
        identity: dict[str, Any],
        *,
        client: RawHttpResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any] | None:
        inventory_id = self._resolve_inventory_id(identity, fk=fk)
        return _find_unique(
            client,
            path=f"inventories/{inventory_id}/{awx_api_path(spec)}/",
            name=str(identity["name"]),
            kind=spec.kind,
            ambiguity_label={"inventory": str(_parent(identity).name)},
        )

    def create(
        self,
        spec: ResourceSpec,
        payload: dict[str, Any],
        identity: dict[str, Any],
        *,
        client: RawHttpResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any]:
        inventory_id = self._resolve_inventory_id(identity, fk=fk)
        path = f"inventories/{inventory_id}/{awx_api_path(spec)}/"
        body = {"name": identity["name"], **payload}
        return client.request("POST", path, json=body)

    @staticmethod
    def _resolve_inventory_id(identity: dict[str, Any], *, fk: FkResolver) -> int:
        parent = _parent(identity)
        scope = {"organization": parent.organization} if parent.organization else None
        return fk.name_to_id("Inventory", parent.name, scope=scope)


def _find_unique(
    client: RawHttpResourceClient,
    *,
    path: str,
    name: str,
    kind: str,
    ambiguity_label: dict[str, str],
) -> dict[str, Any] | None:
    """Resolve a unique record at ``path`` filtered by ``name``.

    Requests two records to detect ambiguity (mirrors
    :meth:`ResourceRepository.find` for the nested-endpoint paths
    that don't fit the spec-driven CRUD shape).
    """
    page = client.request("GET", path, params={"name": name, "page_size": "2"})
    results = page.get("results") or []
    if len(results) >= 2:
        raise AmbiguousIdentityError(
            kind,
            {"name": name, **ambiguity_label},
            match_count=page.get("count"),
        )
    return results[0] if results else None


def _parent(identity: dict[str, Any]) -> Any:
    parent = identity.get("parent")
    if parent is None:
        raise BadRequest(
            "Host and Group docs require metadata.parent (kind: Inventory) — "
            "see examples/inventory-prod.yml"
        )
    if hasattr(parent, "kind") and parent.kind != "Inventory":
        raise BadRequest(
            f"Host and Group docs require metadata.parent.kind == 'Inventory' (got {parent.kind!r})"
        )
    return parent


def _as_dict(value: Any) -> dict[str, Any]:
    """Lift a Pydantic IdentityRef (or dict) to a plain dict for resolution."""
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    return dict(value)
