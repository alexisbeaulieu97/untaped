"""Use case: produce a canonical :class:`Resource` envelope for an AWX object.

Save extracts fields declared in ``spec.canonical_fields``, augments
sub-endpoint membership fields where needed, strips read-only fields, and
translates FK IDs back to human names so the resulting file is portable
across AWX instances.

Schedule's polymorphic parent is extracted from AWX's
``unified_job_template`` + ``summary_fields`` so it ends up in
``metadata.parent`` (an :class:`IdentityRef`) rather than the spec body.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from untaped.capabilities.awx.application.mutation_values import redact_value
from untaped.capabilities.awx.application.ports import FkResolver, ResourceClient
from untaped.capabilities.awx.domain import IdentityRef, Metadata, Resource, ResourceSpec
from untaped.capabilities.awx.errors import BadRequest, ResourceNotFound

_MetadataExtractor = Callable[[ResourceSpec, dict[str, Any], FkResolver], Metadata]

# AWX's snake_case "unified_job_type" → our PascalCase kind names.
_UJT_KIND_MAP: dict[str, str] = {
    "job_template": "JobTemplate",
    "workflow_job_template": "WorkflowJobTemplate",
    "project": "Project",
    "inventory_source": "InventorySource",
}


@dataclass(frozen=True)
class ResourceSnapshot:
    """Portable projection plus FK IDs and member rows captured by those same reads.

    The editor defensively copies this snapshot before launching its process;
    save callers use only the portable resource.
    """

    resource: Resource
    fk_ids: dict[str, Any]
    memberships: dict[str, tuple[dict[str, Any], ...]]


class SaveResource:
    def __init__(self, client: ResourceClient, fk: FkResolver) -> None:
        self._client = client
        self._fk = fk

    def __call__(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None = None,
    ) -> Resource:
        record = self._client.find_by_identity(spec, name=name, scope=scope)
        if record is None:
            raise ResourceNotFound(spec.kind, {"name": name, **(scope or {})})
        return self.snapshot_from_record(spec, record.model_dump()).resource

    def find_all(
        self,
        spec: ResourceSpec,
        *,
        params: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return every record of ``spec.kind`` matching ``params`` (no pagination cap).

        Used by ``save --all-kinds``. Params are passed verbatim to AWX so the
        caller (the CLI's ``--filter`` flag) can use any Django-style
        lookup the API supports.
        """
        return list(self._client.list(spec, params=params or None))

    def from_record(self, spec: ResourceSpec, record: dict[str, Any]) -> Resource:
        """Public access to the record→resource builder for bulk save flows."""
        return self.snapshot_from_record(spec, record).resource

    def metadata_from_record(self, spec: ResourceSpec, record: dict[str, Any]) -> Metadata:
        """Extract ancestry without reading unrelated fields or memberships."""
        return _METADATA_EXTRACTORS.get(spec.kind, _default_metadata)(spec, record, self._fk)

    def snapshot_from_record(self, spec: ResourceSpec, record: dict[str, Any]) -> ResourceSnapshot:
        """Capture display labels and their original IDs without a second member read."""
        if spec.kind == "Inventory" and record.get("kind") == "constructed":
            record = self._client.get(spec, int(record["id"])).model_dump()
        fk_ids = {
            ref.field: copy.deepcopy(record[ref.field])
            for ref in spec.fk_refs
            if ref.field in record and not ref.polymorphic
        }
        memberships: dict[str, tuple[dict[str, Any], ...]] = {}
        spec_data = self._build_spec_body(spec, record)
        if spec.kind == "Inventory" and record.get("kind") == "constructed":
            spec_data.pop("host_filter", None)
        if spec.kind == "InventorySource" and record.get("source") == "constructed":
            spec_data = {
                k: v
                for k, v in spec_data.items()
                if k in {"source", "source_vars", "update_cache_timeout", "limit", "verbosity"}
            }
        # Sub-endpoint multi-FKs (Group.hosts / Group.children /
        # JobTemplate.credentials) are managed via associate/disassociate
        # POSTs against ``/<id>/<sub>/`` rather than the body. For
        # full-fidelity save we still need to read them so the saved YAML
        # can reconstruct the membership on restore.
        record_id = record.get("id")
        if isinstance(record_id, int):
            for ref in spec.fk_refs:
                if ref.field == "input_inventories" and record.get("kind") != "constructed":
                    continue
                if not (ref.multi and ref.sub_endpoint):
                    continue
                members = list(
                    self._client.paginate_sub_endpoint(spec, record_id, ref.sub_endpoint)
                )
                memberships[ref.field] = tuple(copy.deepcopy(members))
                fk_ids[ref.field] = [int(member["id"]) for member in members]
                if ref.kind == "Inventory":
                    spec_data[ref.field] = [
                        self._fk.id_to_identity("Inventory", int(m["id"])).model_dump(
                            exclude_none=True
                        )
                        for m in members
                    ]
                else:
                    spec_data[ref.field] = [
                        str(m["name"]) for m in members if isinstance(m.get("name"), str)
                    ]
        metadata = self.metadata_from_record(spec, record)
        # Polymorphic FK lives in metadata; strip from spec body if present
        for fk in spec.fk_refs:
            if fk.polymorphic:
                spec_data.pop(fk.field, None)
        return ResourceSnapshot(
            resource=Resource(
                kind=spec.kind,
                metadata=metadata,
                spec=redact_value(spec_data, spec.secret_paths, replacement="$encrypted$"),
            ),
            fk_ids=fk_ids,
            memberships=memberships,
        )

    def _build_spec_body(self, spec: ResourceSpec, record: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {}
        for field in spec.canonical_fields:
            if field not in record:
                continue
            body[field] = record[field]
        for fk in spec.fk_refs:
            if fk.polymorphic or fk.field not in body or body[fk.field] is None:
                continue
            assert fk.kind is not None  # non-polymorphic FK always has a kind
            value = body[fk.field]
            if fk.multi:
                if isinstance(value, list):
                    body[fk.field] = [self._fk.id_to_name(fk.kind, int(v)) for v in value]
            else:
                body[fk.field] = self._fk.id_to_name(fk.kind, int(value))
        return body


def _default_metadata(spec: ResourceSpec, record: dict[str, Any], fk: FkResolver) -> Metadata:
    name = str(record["name"])
    if "organization" in spec.identity_keys and record.get("organization") is not None:
        org_name = fk.id_to_name("Organization", int(record["organization"]))
        return Metadata(name=name, organization=org_name)
    return Metadata(name=name)


def _schedule_metadata(spec: ResourceSpec, record: dict[str, Any], fk: FkResolver) -> Metadata:
    """Schedule's parent is reconstructed from ``summary_fields``.

    AWX returns ``unified_job_template: <id>`` plus
    ``summary_fields.unified_job_template.{name, unified_job_type}``.
    We translate that back to an :class:`IdentityRef`.
    """
    name = str(record["name"])
    summary = record.get("summary_fields") or {}
    parent_summary = summary.get("unified_job_template") or {}
    parent_kind_str = parent_summary.get("unified_job_type")
    parent_name = parent_summary.get("name")
    parent_kind = _UJT_KIND_MAP.get(parent_kind_str or "")
    parent: IdentityRef | None = None
    parent_id = record.get("unified_job_template") or parent_summary.get("id")
    if parent_kind == "InventorySource" and not isinstance(parent_id, int):
        raise BadRequest("cannot save source-parent schedule without inventory ancestry")
    if isinstance(parent_id, int) and (
        not parent_kind
        or not parent_name
        or parent_kind == "InventorySource"
        or not parent_summary.get("organization_name")
    ):
        parent = fk.id_to_identity(parent_kind or "UnifiedJobTemplate", parent_id)
        return Metadata(name=name, parent=parent)
    if parent_kind and parent_name:
        # Schedule parents may belong to an organization; preserve it when present.
        parent_org = parent_summary.get("organization_name")
        parent = IdentityRef(kind=parent_kind, name=parent_name, organization=parent_org)
    if parent is None:
        raise BadRequest("cannot save schedule without resolvable parent ancestry")
    return Metadata(name=name, parent=parent)


def _inventory_child_metadata(
    spec: ResourceSpec, record: dict[str, Any], fk: FkResolver
) -> Metadata:
    """Reconstruct ``metadata.parent`` (Inventory) for Host/Group.

    Inventory-child kinds carry their parent FK as ``record["inventory"]``
    plus the denormalised ``summary_fields.inventory.{name,organization_name}``
    that AWX returns. Without this extractor a saved Host/Group has no
    ``metadata.parent`` and ``InventoryChildApplyStrategy`` rejects the
    restore with ``identity missing 'parent'``.
    """
    name = str(record["name"])
    summary = record.get("summary_fields") or {}
    inv_summary = summary.get("inventory") or {}
    parent_name = inv_summary.get("name")
    parent_org = inv_summary.get("organization_name")
    if isinstance(parent_name, str) and isinstance(parent_org, str):
        parent = IdentityRef(
            kind="Inventory",
            name=parent_name,
            organization=parent_org if isinstance(parent_org, str) else None,
        )
        return Metadata(name=name, parent=parent)
    # Fallback: resolve via the FK id so a fixture without summary_fields
    # still produces a usable saved file (parent.organization may be unset
    # because Inventory's own record is the only place it's stored).
    inventory_id = record.get("inventory")
    if isinstance(inventory_id, int):
        return Metadata(name=name, parent=fk.id_to_identity("Inventory", inventory_id))
    raise BadRequest(f"cannot save {spec.kind} without inventory ancestry")


_METADATA_EXTRACTORS: dict[str, _MetadataExtractor] = {
    "Schedule": _schedule_metadata,
    "Host": _inventory_child_metadata,
    "InventorySource": _inventory_child_metadata,
    "Group": _inventory_child_metadata,
}
"""Per-kind hooks for non-default metadata extraction.

Keep this map small: most kinds use ``_default_metadata`` via
``identity_keys = ("name", "organization")``. Schedule, Host, and Group
are the v0.5 users; new kinds with non-default identity should add an
entry here rather than letting saves silently lose ``metadata.parent``.
"""
