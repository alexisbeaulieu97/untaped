"""Build the identity dict and resolved payload for the apply pipeline.

:class:`ApplyPlanner` is the first collaborator the orchestrator hits:
it projects ``resource.spec`` onto ``spec.canonical_fields``, resolves
FK names to ids, drops sub-endpoint multi-FKs (those go through the
membership reconciler, not PATCH), and returns the identity dict
strategies use to ``find_existing``.

:func:`scope_for` is the FK-lookup-scope helper used by every caller in
the apply pipeline that resolves FK names: the planner itself,
``MembershipReconciler.plan`` (``apply_membership.py``), and
``apply_file._prefetch_plan`` (bulk warm-up). Pure module-level function
— sharing it keeps the prefetch path's cache buckets aligned with what
the apply path actually queries.
"""

from __future__ import annotations

from typing import Any

from untaped_awx.application.ports import FkResolver
from untaped_awx.domain import FkRef, Resource, ResourceSpec


class ApplyPlanner:
    """Identity + payload preparation for the apply pipeline."""

    def plan_identity(self, spec: ResourceSpec, resource: Resource) -> dict[str, Any]:
        """Identity is whichever metadata fields uniquely identify the resource.

        Default: ``{name, organization}``. Schedule and inventory-child
        kinds (Host, Group) include ``parent``.
        """
        identity: dict[str, Any] = {"name": resource.metadata.name}
        if "organization" in spec.identity_keys:
            identity["organization"] = resource.metadata.organization
        if resource.metadata.parent is not None:
            identity["parent"] = resource.metadata.parent
        return identity

    def plan_payload(
        self, spec: ResourceSpec, resource: Resource, *, fk: FkResolver
    ) -> dict[str, Any]:
        """Project ``resource.spec`` onto ``spec.canonical_fields`` and resolve FKs."""
        body: dict[str, Any] = {}
        raw = resource.spec
        for field in spec.canonical_fields:
            if field in raw:
                body[field] = raw[field]
        # Inject identity keys from metadata so create payloads include
        # ``name`` (and ``organization`` for org-scoped kinds) even when
        # absent from spec.
        for key in spec.identity_keys:
            if key in body:
                continue
            value = getattr(resource.metadata, key, None)
            if value is not None:
                body[key] = value
        # Resolve FKs (skip polymorphic — those live in metadata, not
        # payload — and skip sub_endpoint multi-FKs, which are managed
        # out-of-band via associate / disassociate POSTs against
        # ``/<api_path>/<id>/<sub>/``).
        for ref in spec.fk_refs:
            if ref.polymorphic or ref.field not in body or body[ref.field] is None:
                continue
            if ref.multi and ref.sub_endpoint is not None:
                # Membership goes through the reconciler; never PATCH it.
                body.pop(ref.field, None)
                continue
            assert ref.kind is not None
            scope = scope_for(ref, resource)
            value = body[ref.field]
            if ref.multi:
                if isinstance(value, list):
                    body[ref.field] = [fk.name_to_id(ref.kind, str(v), scope=scope) for v in value]
            else:
                body[ref.field] = fk.name_to_id(ref.kind, str(value), scope=scope)
        return body


def scope_for(ref: FkRef, resource: Resource) -> dict[str, str] | None:
    """Return the FK lookup scope for ``ref`` against ``resource``.

    Centralised so the apply path and ``apply_file._prefetch_plan``
    (bulk warm-up) read the same semantics — otherwise prefetch warms
    the wrong cache buckets for inventory-child kinds
    (``hosts``/``children``) whose scope lives on ``metadata.parent``
    rather than in the body.
    """
    if ref.scope_field is None:
        return None
    if ref.scope_field == "organization":
        # For Schedule (and any future kind whose canonical org lives on
        # the polymorphic parent), prefer ``parent.organization`` so
        # name-scoped FK lookups resolve in the parent's org, not the
        # schedule's own (which is typically ``None``).
        org = (
            resource.metadata.parent.organization if resource.metadata.parent else None
        ) or resource.metadata.organization
        if org:
            return {"organization": org}
    if ref.scope_field == "inventory":
        # Hosts and Groups (and their group-membership FKs) are scoped
        # by inventory, not org. The inventory lives on
        # ``metadata.parent`` for ``inventory_child`` kinds — there's no
        # separate metadata field for it because Schedule's
        # polymorphic-parent envelope already carries everything we
        # need. When the parent's org is set, also scope by
        # ``inventory__organization`` so AWX disambiguates same-named
        # inventories across orgs (it expands to
        # ``?inventory__name=…&inventory__organization__name=…`` — the
        # only way to disambiguate Host/Group ancestry on AWX's filter
        # surface, since hosts don't carry a direct ``organization`` FK).
        parent = resource.metadata.parent
        if parent is not None and parent.kind == "Inventory":
            scope: dict[str, str] = {"inventory": parent.name}
            if parent.organization:
                scope["inventory__organization"] = parent.organization
            return scope
    return None
