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

from untaped.capabilities.awx.domain import Resource, ResourceSpec, WritePayload
from untaped.capabilities.awx.domain.inventory import (
    CONSTRUCTED_SOURCE_FIELDS,
    inventory_read_only_fields,
)
from untaped.capabilities.awx.errors import AmbiguousIdentityError, BadRequest
from untaped.capabilities.awx.infrastructure.spec import awx_api_path

if TYPE_CHECKING:
    from untaped.capabilities.awx.application.mutation_refs import PlannedId
    from untaped.capabilities.awx.application.ports import FkResolver, RawHttpResourceClient


class DefaultApplyStrategy:
    """Plain CRUD against ``<api_path>/``.

    Uses AWX's ``<scope_field>__name=<value>`` syntax to find existing
    resources by their identity (so we don't have to pre-resolve scope
    IDs just to look up).
    """

    def snapshot_existing(
        self, spec: ResourceSpec, existing: dict[str, Any], *, client: RawHttpResourceClient
    ) -> dict[str, Any]:
        return existing

    def prepare_state(
        self,
        spec: ResourceSpec,
        resource: Resource,
        existing: dict[str, Any] | None,
        *,
        parent: tuple[str, PlannedId] | None = None,
        parent_resource: Resource | None = None,
        client: RawHttpResourceClient,
    ) -> tuple[ResourceSpec, dict[str, Any] | None]:
        return spec, existing

    def prepare_parent(
        self, spec: ResourceSpec, identity: dict[str, Any], *, fk: FkResolver
    ) -> tuple[str, PlannedId] | None:
        return None

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

    def prepare_parent(
        self, spec: ResourceSpec, identity: dict[str, Any], *, fk: FkResolver
    ) -> tuple[str, PlannedId] | None:
        parent = identity.get("parent")
        if parent is None:
            raise BadRequest("schedule identity missing 'parent'")
        result = fk.resolve_polymorphic(_as_dict(parent))
        self._parent_path(result[0])
        return result

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
        parent_kind, parent_id = identity.get("_prepared_parent") or fk.resolve_polymorphic(
            _as_dict(parent)
        )
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
        parent_kind, parent_id = identity["_prepared_parent"]
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

    def prepare_parent(
        self, spec: ResourceSpec, identity: dict[str, Any], *, fk: FkResolver
    ) -> tuple[str, PlannedId] | None:
        return "Inventory", self._resolve_inventory_id(identity, fk=fk)

    def find_existing(
        self,
        spec: ResourceSpec,
        identity: dict[str, Any],
        *,
        client: RawHttpResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any] | None:
        inventory_id = (
            identity["_prepared_parent"][1]
            if "_prepared_parent" in identity
            else self._resolve_inventory_id(identity, fk=fk)
        )
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
        inventory_id = identity["_prepared_parent"][1]
        path = f"inventories/{inventory_id}/{awx_api_path(spec)}/"
        body = {"name": identity["name"], **payload}
        if spec.kind == "InventorySource":
            inventory = client.request("GET", f"inventories/{inventory_id}/")
            if inventory.get("kind") == "constructed":
                invalid = set(payload) - {*CONSTRUCTED_SOURCE_FIELDS, "source", "name"}
                if invalid or payload.get("source", "constructed") != "constructed":
                    raise BadRequest(
                        "cannot create an independent source on a constructed inventory"
                    )
                generated = _generated_source(client, inventory_id)
                return client.request(
                    "PATCH",
                    f"inventory_sources/{generated['id']}/",
                    json={k: v for k, v in payload.items() if k in CONSTRUCTED_SOURCE_FIELDS},
                )
        return client.request("POST", path, json=body)

    def prepare_state(
        self,
        spec: ResourceSpec,
        resource: Resource,
        existing: dict[str, Any] | None,
        *,
        parent: tuple[str, PlannedId] | None = None,
        parent_resource: Resource | None = None,
        client: RawHttpResourceClient,
    ) -> tuple[ResourceSpec, dict[str, Any] | None]:
        if spec.kind != "InventorySource":
            return spec, existing
        inventory_kind = None
        if parent is not None and isinstance(parent[1], int):
            inventory_kind = client.request("GET", f"inventories/{parent[1]}/").get("kind", "")
        elif parent_resource is not None:
            inventory_kind = parent_resource.spec.get("kind", "")
        if inventory_kind == "smart" and existing is None:
            raise BadRequest("smart inventories do not support inventory sources")
        generated = inventory_kind == "constructed" or (
            existing is not None and existing.get("source") == "constructed"
        )
        if not generated:
            if resource.spec.get("source") == "constructed":
                raise BadRequest("constructed sources require a constructed inventory")
            return spec, existing
        if existing is None and parent is not None and isinstance(parent[1], int):
            existing = _generated_source(client, parent[1])
        invalid = set(resource.spec) - {*CONSTRUCTED_SOURCE_FIELDS, "source"}
        if invalid:
            raise BadRequest(
                "generated constructed source only permits " + ", ".join(CONSTRUCTED_SOURCE_FIELDS)
            )
        if resource.spec.get("source", "constructed") != "constructed":
            raise BadRequest("generated constructed source type cannot change")
        spec = spec.model_copy(
            update={
                "read_only_fields": (
                    *spec.read_only_fields,
                    *inventory_read_only_fields(spec.kind, {"source": "constructed"}),
                ),
                "singleton_parent": True,
                "parent_field_aliases": CONSTRUCTED_SOURCE_FIELDS,
            }
        )
        return spec, existing

    @staticmethod
    def _resolve_inventory_id(identity: dict[str, Any], *, fk: FkResolver) -> PlannedId:
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
            "Inventory child docs require metadata.parent (kind: Inventory) — "
            "see examples/inventory-prod.yml"
        )
    if hasattr(parent, "kind") and parent.kind != "Inventory":
        raise BadRequest(
            "Inventory child docs require metadata.parent.kind == 'Inventory' "
            f"(got {parent.kind!r})"
        )
    return parent


def _as_dict(value: Any) -> dict[str, Any]:
    """Lift a Pydantic IdentityRef (or dict) to a plain dict for resolution."""
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    return dict(value)


class InventoryApplyStrategy(DefaultApplyStrategy):
    """Freeze constructed proxy routing using the selected inventory ID."""

    def snapshot_existing(
        self, spec: ResourceSpec, existing: dict[str, Any], *, client: RawHttpResourceClient
    ) -> dict[str, Any]:
        if existing.get("kind") != "constructed":
            return existing
        hydrated = client.get(spec, int(existing["id"])).model_dump()
        if hydrated.get("id") != existing["id"]:
            raise BadRequest("constructed inventory hydration changed selected ID")
        return hydrated

    def prepare_state(
        self,
        spec: ResourceSpec,
        resource: Resource,
        existing: dict[str, Any] | None,
        *,
        parent: tuple[str, PlannedId] | None = None,
        parent_resource: Resource | None = None,
        client: RawHttpResourceClient,
    ) -> tuple[ResourceSpec, dict[str, Any] | None]:
        kind = existing.get("kind", "") if existing is not None else resource.spec.get("kind", "")
        if existing is not None and "kind" in resource.spec and resource.spec["kind"] != kind:
            raise BadRequest("inventory kind cannot change on update")
        if kind == "constructed":
            if resource.spec.get("host_filter") not in (None, ""):
                raise BadRequest("constructed inventories do not support host_filter")
            spec = spec.model_copy(
                update={
                    "api_path": "constructed_inventories",
                    "read_only_fields": (
                        *spec.read_only_fields,
                        *inventory_read_only_fields(spec.kind, {"kind": kind}),
                    ),
                }
            )
        elif any(
            field in resource.spec for field in (*CONSTRUCTED_SOURCE_FIELDS, "input_inventories")
        ):
            raise BadRequest("constructed inventory settings require kind=constructed")
        return spec, existing


def _generated_source(client: RawHttpResourceClient, inventory_id: int) -> dict[str, Any]:
    """Read the one managed source, including after a same-batch inventory create."""
    sources = (
        client.request("GET", f"inventories/{inventory_id}/inventory_sources/").get("results") or []
    )
    if len(sources) != 1 or sources[0].get("source") != "constructed":
        raise BadRequest("constructed inventory has no unique generated source")
    return dict(sources[0])
