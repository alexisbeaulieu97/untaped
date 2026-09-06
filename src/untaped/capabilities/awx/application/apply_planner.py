"""Build the identity dict and resolved payload for the apply pipeline.

:class:`ApplyPlanner` is the first collaborator the orchestrator hits:
it passes ``resource.spec`` through minus the apply drop-set, resolves
FK names to ids, drops sub-endpoint multi-FKs (those go through the
membership reconciler, not PATCH), and returns the identity dict
strategies use to ``find_existing``.

:func:`scope_for` is the FK-lookup-scope helper used by every caller in
the apply pipeline that resolves FK names: the planner itself,
``MembershipReconciler.plan`` (``apply_membership.py``), and
``apply_prefetch.prefetch_plan`` (bulk warm-up). Pure module-level function
— sharing it keeps the prefetch path's cache buckets aligned with what
the apply path actually queries.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from untaped.capabilities.awx.application.ports import FkResolver
from untaped.capabilities.awx.domain import FkRef, Resource, ResourceSpec


def unrecognized_fields(spec: ResourceSpec, names: Iterable[str]) -> list[str]:
    """Names not part of ``spec``'s known schema (:attr:`ResourceSpec.known_fields`), sorted.

    Under the passthrough payload model these fields are still *sent* (minus the
    :meth:`ApplyPlanner.plan_payload` drop-set), but the caller warns about them
    so a hand-typed typo or a field this tool has no metadata for stays visible
    rather than silently no-op'ing on the server.
    """
    return sorted(name for name in names if name not in spec.known_fields)


def unrecognized_warning(spec: ResourceSpec, names: Iterable[str]) -> str | None:
    """The shared "field(s) sent as-is" warning body, or ``None`` if all known.

    One source of truth for the message that both the file-mode
    (:meth:`ApplyResource._warn_unrecognized`, per doc) and ``--stdin``
    (:func:`run_apply_stdin`, once per overlay) paths emit. Callers add their own
    ``warning:`` prefix / routing.
    """
    unknown = unrecognized_fields(spec, names)
    if not unknown:
        return None
    return f"{spec.kind}: field(s) sent as-is (not in this tool's known schema): " + ", ".join(
        unknown
    )


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
        """Pass ``resource.spec`` through (minus a drop-set) and resolve FKs.

        Passthrough rather than a closed ``canonical_fields`` allowlist, so
        fields a given AWX version accepts work without a spec change. The
        drop-set excludes fields that must never be PATCHed straight from the
        spec body:

        - ``read_only_fields`` — server-managed (e.g. left over in a get-export);
        - ``identity_keys`` — identity lives in ``metadata``, never the spec, so
          a stray ``spec.name``/``spec.organization`` can't override it;
        - polymorphic FK fields (e.g. Schedule ``parent``) — carried on metadata;
        - ``sub_endpoint`` multi-FKs (e.g. Group ``hosts``, JobTemplate
          ``credentials``) — reconciled out-of-band via
          associate/disassociate POSTs, not the body.
        """
        raw = resource.spec
        # FK fields handled out-of-band (polymorphic ⇒ metadata; sub_endpoint
        # multi ⇒ membership reconciler) must never be PATCHed from the body.
        out_of_band_fks = {
            ref.field
            for ref in spec.fk_refs
            if ref.polymorphic or (ref.multi and ref.sub_endpoint is not None)
        }
        drop = set(spec.read_only_fields) | set(spec.identity_keys) | out_of_band_fks
        body: dict[str, Any] = {field: value for field, value in raw.items() if field not in drop}
        # Inject identity keys from metadata so create payloads include
        # ``name`` (and ``organization`` for org-scoped kinds), and identity is
        # always metadata-sourced (never overridable by the spec body).
        for key in spec.identity_keys:
            value = getattr(resource.metadata, key, None)
            if value is not None:
                body[key] = value
        # Resolve the FK names that survived the drop-set. Polymorphic and
        # sub_endpoint multi-FKs were excluded above, so every ref reaching here
        # has a concrete ``kind`` and belongs in the body.
        for ref in spec.fk_refs:
            if ref.field not in body or body[ref.field] is None:
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

    Centralised so the apply path and ``apply_prefetch.prefetch_plan``
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
