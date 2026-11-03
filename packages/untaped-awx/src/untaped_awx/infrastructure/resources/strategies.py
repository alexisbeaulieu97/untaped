"""Concrete :class:`ApplyStrategy` implementations.

The default strategy works for any kind whose write path is plain CRUD
on its own collection. Schedule has its own strategy because a schedule is
created under its template.

Strategies bridge the dict-shaped payloads produced by application use
cases to the typed :class:`ResourceClient` boundary: dicts are wrapped
in :class:`WritePayload` on the way out, and :class:`ServerRecord`
results from the client are flattened to dict for the apply pipeline's
in-place strip / diff / preserve passes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from untaped_awx.domain import Resource, ResourceSpec, WritePayload
from untaped_awx.domain.inventory import (
    CONSTRUCTED_SOURCE_FIELDS,
    inventory_read_only_fields,
)
from untaped_awx.domain.kinds import UNIFIED_TEMPLATE_KINDS
from untaped_awx.domain.payloads import as_dict
from untaped_awx.errors import AmbiguousIdentityError, BadRequestError

if TYPE_CHECKING:
    from untaped_awx.application.resources.mutation_refs import PlannedId
    from untaped_awx.application.resources.ports import FkResolver, NestedResourceClient


class DefaultApplyStrategy:
    """Plain CRUD on the kind's own collection.

    Uses AWX's ``<scope_field>__name=<value>`` syntax to find existing
    resources by their identity (so we don't have to pre-resolve scope
    IDs just to look up).
    """

    def snapshot_existing(
        self, spec: ResourceSpec, existing: dict[str, Any], *, client: NestedResourceClient
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
        client: NestedResourceClient,
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
        client: NestedResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any] | None:
        params: dict[str, str] = {}
        for key, value in identity.items():
            if value is None:
                continue
            if key == "name":
                params["name"] = str(value)
            elif key.endswith("__isnull"):
                params[key] = "true" if value else "false"
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
        client: NestedResourceClient,
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
        client: NestedResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any]:
        record = client.update(spec, existing["id"], WritePayload(**payload))
        return record.model_dump()


class ScheduleApplyStrategy(DefaultApplyStrategy):
    """A schedule is created under its template.

    AWX creates a schedule only under its template; updates go through the
    schedule itself and so reuse :meth:`DefaultApplyStrategy.update`.
    Identity is ``(name, parent)`` where ``parent`` is the polymorphic
    IdentityRef from ``resource.metadata.parent``.
    """

    def prepare_parent(
        self, spec: ResourceSpec, identity: dict[str, Any], *, fk: FkResolver
    ) -> tuple[str, PlannedId] | None:
        parent = identity.get("parent")
        if parent is None:
            raise BadRequestError("schedule identity missing 'parent'")
        result = fk.resolve_polymorphic(as_dict(parent))
        _check_schedule_parent(result[0])
        return result

    def find_existing(
        self,
        spec: ResourceSpec,
        identity: dict[str, Any],
        *,
        client: NestedResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any] | None:
        parent = identity.get("parent")
        if parent is None:
            raise BadRequestError("schedule identity missing 'parent'")
        parent_kind, parent_id = identity.get("_prepared_parent") or fk.resolve_polymorphic(
            as_dict(parent)
        )
        _check_schedule_parent(parent_kind)
        return _find_unique(
            client,
            spec,
            (parent_kind, _existing(parent_id)),
            name=str(identity["name"]),
            ambiguity_label={"parent": f"{parent_kind}#{parent_id}"},
        )

    def create(
        self,
        spec: ResourceSpec,
        payload: dict[str, Any],
        identity: dict[str, Any],
        *,
        client: NestedResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any]:
        parent = identity.get("parent")
        if parent is None:
            raise BadRequestError("schedule identity missing 'parent' for create")
        parent_kind, parent_id = identity["_prepared_parent"]
        _check_schedule_parent(parent_kind)
        return client.create_under(
            spec, parent_kind, parent_id, {"name": identity["name"], **payload}
        )


def _check_schedule_parent(parent_kind: str) -> None:
    """A schedule's parent is a unified template."""
    if parent_kind not in UNIFIED_TEMPLATE_KINDS:
        raise BadRequestError(
            f"schedule parent kind {parent_kind!r} not supported "
            f"(use one of {sorted(UNIFIED_TEMPLATE_KINDS)})"
        )


class InventoryChildApplyStrategy(DefaultApplyStrategy):
    """Write path for resources whose parent is an Inventory.

    Used by :data:`HOST_SPEC` and :data:`GROUP_SPEC`. AWX creates one
    either in the kind's own collection (with ``inventory: <id>`` in the
    body) or under its inventory (which fills the FK in). We create it
    under its inventory, so the user's spec body never carries
    ``inventory``, a redundant FK ``metadata.parent`` already implies.
    Updates reuse :meth:`DefaultApplyStrategy.update`.
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
        client: NestedResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any] | None:
        inventory_id = (
            identity["_prepared_parent"][1]
            if "_prepared_parent" in identity
            else self._resolve_inventory_id(identity, fk=fk)
        )
        return _find_unique(
            client,
            spec,
            ("Inventory", _existing(inventory_id)),
            name=str(identity["name"]),
            ambiguity_label={"inventory": str(_parent(identity).name)},
        )

    def create(
        self,
        spec: ResourceSpec,
        payload: dict[str, Any],
        identity: dict[str, Any],
        *,
        client: NestedResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any]:
        inventory_id = identity["_prepared_parent"][1]
        body = {"name": identity["name"], **payload}
        if spec.kind == "InventorySource" and client.inventory_kind(inventory_id) == "constructed":
            invalid = set(payload) - {*CONSTRUCTED_SOURCE_FIELDS, "source", "name"}
            if invalid or payload.get("source", "constructed") != "constructed":
                raise BadRequestError(
                    "cannot create an independent source on a constructed inventory"
                )
            generated = _generated_source(client, spec, inventory_id)
            settings = {k: v for k, v in payload.items() if k in CONSTRUCTED_SOURCE_FIELDS}
            return client.update(spec, int(generated["id"]), WritePayload(**settings)).model_dump()
        return client.create_under(spec, "Inventory", inventory_id, body)

    def prepare_state(
        self,
        spec: ResourceSpec,
        resource: Resource,
        existing: dict[str, Any] | None,
        *,
        parent: tuple[str, PlannedId] | None = None,
        parent_resource: Resource | None = None,
        client: NestedResourceClient,
    ) -> tuple[ResourceSpec, dict[str, Any] | None]:
        if spec.kind != "InventorySource":
            return spec, existing
        inventory_kind = None
        if parent is not None and isinstance(parent[1], int):
            inventory_kind = client.inventory_kind(parent[1])
        elif parent_resource is not None:
            inventory_kind = parent_resource.spec.get("kind", "")
        if inventory_kind == "smart" and existing is None:
            raise BadRequestError("smart inventories do not support inventory sources")
        generated = inventory_kind == "constructed" or (
            existing is not None and existing.get("source") == "constructed"
        )
        if not generated:
            if resource.spec.get("source") == "constructed":
                raise BadRequestError("constructed sources require a constructed inventory")
            return spec, existing
        if existing is None and parent is not None and isinstance(parent[1], int):
            existing = _generated_source(client, spec, parent[1])
        invalid = set(resource.spec) - {*CONSTRUCTED_SOURCE_FIELDS, "source"}
        if invalid:
            raise BadRequestError(
                "generated constructed source only permits " + ", ".join(CONSTRUCTED_SOURCE_FIELDS)
            )
        if resource.spec.get("source", "constructed") != "constructed":
            raise BadRequestError("generated constructed source type cannot change")
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


def _existing(planned: PlannedId) -> int:
    """``planned`` as a record id: a lookup runs only under a parent that exists."""
    if not isinstance(planned, int):
        raise BadRequestError("cannot look under a parent the batch has yet to create")
    return planned


def _find_unique(
    client: NestedResourceClient,
    spec: ResourceSpec,
    parent: tuple[str, int],
    *,
    name: str,
    ambiguity_label: dict[str, str],
) -> dict[str, Any] | None:
    """The one ``spec`` record named ``name`` under ``parent``, if any.

    Asks for two records to detect ambiguity, as
    :meth:`ResourceRepository.find` does for a kind's own collection.
    """
    parent_kind, parent_id = parent
    results, count = client.named_under(spec, parent_kind, parent_id, name=name)
    if len(results) >= 2:
        identity = {"name": name, **ambiguity_label}
        raise AmbiguousIdentityError(spec.kind, identity, match_count=count)
    return results[0] if results else None


def _parent(identity: dict[str, Any]) -> Any:
    parent = identity.get("parent")
    if parent is None:
        raise BadRequestError(
            "inventory child documents require metadata.parent (kind: Inventory); "
            "see examples/inventory-prod.yml"
        )
    if hasattr(parent, "kind") and parent.kind != "Inventory":
        raise BadRequestError(
            "inventory child documents require metadata.parent.kind == 'Inventory' "
            f"(got {parent.kind!r})"
        )
    return parent


class InventoryApplyStrategy(DefaultApplyStrategy):
    """Freeze constructed proxy routing using the selected inventory ID."""

    def snapshot_existing(
        self, spec: ResourceSpec, existing: dict[str, Any], *, client: NestedResourceClient
    ) -> dict[str, Any]:
        if existing.get("kind") != "constructed":
            return existing
        hydrated = client.get(spec, int(existing["id"])).model_dump()
        if hydrated.get("id") != existing["id"]:
            raise BadRequestError("constructed inventory hydration changed selected ID")
        return hydrated

    def prepare_state(
        self,
        spec: ResourceSpec,
        resource: Resource,
        existing: dict[str, Any] | None,
        *,
        parent: tuple[str, PlannedId] | None = None,
        parent_resource: Resource | None = None,
        client: NestedResourceClient,
    ) -> tuple[ResourceSpec, dict[str, Any] | None]:
        kind = existing.get("kind", "") if existing is not None else resource.spec.get("kind", "")
        if existing is not None and "kind" in resource.spec and resource.spec["kind"] != kind:
            raise BadRequestError("inventory kind cannot change on update")
        if kind == "constructed":
            if resource.spec.get("host_filter") not in (None, ""):
                raise BadRequestError("constructed inventories do not support host_filter")
            spec = spec.model_copy(
                update={
                    "variant": "constructed",
                    "read_only_fields": (
                        *spec.read_only_fields,
                        *inventory_read_only_fields(spec.kind, {"kind": kind}),
                    ),
                }
            )
        elif any(
            field in resource.spec for field in (*CONSTRUCTED_SOURCE_FIELDS, "input_inventories")
        ):
            raise BadRequestError("constructed inventory settings require kind=constructed")
        return spec, existing


def _generated_source(
    client: NestedResourceClient, spec: ResourceSpec, inventory_id: int
) -> dict[str, Any]:
    """Read the one managed source, including after a same-batch inventory create."""
    sources = list(client.list_under(spec, "Inventory", inventory_id))
    if len(sources) != 1 or sources[0].get("source") != "constructed":
        raise BadRequestError("constructed inventory has no unique generated source")
    return dict(sources[0])
