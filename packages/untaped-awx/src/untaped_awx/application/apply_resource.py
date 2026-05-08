"""Use case: upsert a single :class:`Resource` doc against AWX.

Default behaviour is **preview** — the diff is computed and returned
without writing. Pass ``write=True`` (CLI: ``--yes``) to actually
PATCH/POST. The caller decides whether to gate writes on a confirmation
prompt.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from untaped_awx.application._secret_paths import strip_encrypted
from untaped_awx.application.apply_secret_policy import SecretPreservationPolicy
from untaped_awx.application.ports import (
    ApplyStrategy,
    Catalog,
    FkResolver,
    RawHttpResourceClient,
    ResourceClient,
    StrategyResolver,
)
from untaped_awx.domain import (
    ApplyOutcome,
    FieldChange,
    FkRef,
    Resource,
    ResourceSpec,
)
from untaped_awx.errors import BadRequest


@dataclass(frozen=True)
class _SubEndpointPlan:
    """One reconciled multi-FK relationship.

    ``ref`` describes the spec field (e.g. ``Group.hosts``);
    ``field_change`` is non-None only when ``to_associate`` or
    ``to_disassociate`` is non-empty (so the apply diff stays quiet
    for unmodified memberships).
    """

    ref: FkRef
    to_associate: tuple[int, ...]
    to_disassociate: tuple[int, ...]
    field_change: FieldChange | None


WarnFn = Callable[[str], None]


def _noop_warn(_msg: str) -> None: ...


class ApplyResource:
    def __init__(
        self,
        client: RawHttpResourceClient,
        catalog: Catalog,
        fk: FkResolver,
        strategies: StrategyResolver,
        *,
        warn: WarnFn = _noop_warn,
        secret_policy: SecretPreservationPolicy | None = None,
    ) -> None:
        self._client = client
        self._catalog = catalog
        self._fk = fk
        self._strategies = strategies
        self._warn = warn
        self._secret_policy = secret_policy or SecretPreservationPolicy()

    def __call__(
        self,
        resource: Resource,
        *,
        write: bool = False,
        defer_memberships: bool = False,
    ) -> ApplyOutcome:
        """Apply ``resource`` (preview by default; pass ``write=True`` to write).

        ``defer_memberships=True`` (only meaningful when ``write=True``)
        writes the body but skips ``_execute_sub_endpoints``. Used by
        :class:`ApplyFile` to break cyclic membership dependencies — a
        Group whose ``children:`` references a sibling Group declared
        later in the same file would otherwise fail in phase 1 because
        the sibling doesn't exist yet. After every doc's body has been
        written, :class:`ApplyFile` calls :meth:`reconcile_memberships`
        to drive the deferred writes against now-existing siblings.
        """
        spec = self._catalog.get(resource.kind)
        # ``read_only`` kinds (Credential, Inventory, Organization,
        # CredentialType, plus the catalog-only stubs
        # ExecutionEnvironment/Label/InstanceGroup) are not roundtrippable
        # yet — per-kind sub-apps already hide ``apply``, but the top-level
        # ``untaped awx apply <file>`` reaches this use case directly via
        # ``apply_file`` and would otherwise issue create/update calls for
        # resources whose CRUD is deferred. Reject at the boundary.
        if spec.fidelity == "read_only":
            raise BadRequest(
                f"{spec.kind} does not support apply (fidelity={spec.fidelity!r}); "
                "edit this resource via the AWX UI or API directly."
            )
        identity = _build_identity(spec, resource)
        payload = _build_payload(spec, resource, fk=self._fk)
        strategy = self._strategies.get(spec.apply_strategy)
        existing = strategy.find_existing(spec, identity, client=self._client, fk=self._fk)
        return self._dispatch(
            spec=spec,
            resource=resource,
            identity=identity,
            payload=payload,
            existing=existing,
            strategy=strategy,
            write=write,
            defer_memberships=defer_memberships,
        )

    def reconcile_memberships(self, resource: Resource) -> list[FieldChange]:
        """Phase 2 of two-phase apply: write deferred sub-endpoint memberships.

        Looks up ``resource``'s now-existing record, plans (this is when
        every sibling FK exists, so name → id resolves cleanly), and
        executes associate/disassociate POSTs. Returns the field-change
        rows the caller can splice into the original outcome. Returns
        an empty list when the kind has no sub-endpoint multi-FKs (most
        kinds) so the second pass is essentially free.
        """
        spec = self._catalog.get(resource.kind)
        # Short-circuit kinds without sub-endpoint refs — phase 2 is only
        # meaningful for Group (hosts/children) today. Saves an extra
        # find_existing call per non-Group doc.
        if not any(ref.multi and ref.sub_endpoint for ref in spec.fk_refs):
            return []
        identity = _build_identity(spec, resource)
        strategy = self._strategies.get(spec.apply_strategy)
        existing = strategy.find_existing(spec, identity, client=self._client, fk=self._fk)
        if existing is None:
            raise BadRequest(
                f"{spec.kind} {resource.metadata.name!r}: cannot reconcile "
                f"memberships — record vanished between body write and membership pass"
            )
        membership_plans = _plan_sub_endpoints(
            spec,
            resource,
            int(existing["id"]),
            client=self._client,
            fk=self._fk,
        )
        if any(p.to_associate or p.to_disassociate for p in membership_plans):
            _execute_sub_endpoints(spec, int(existing["id"]), membership_plans, client=self._client)
        return [p.field_change for p in membership_plans if p.field_change is not None]

    def _dispatch(
        self,
        *,
        spec: ResourceSpec,
        resource: Resource,
        identity: dict[str, Any],
        payload: dict[str, Any],
        existing: dict[str, Any] | None,
        strategy: ApplyStrategy,
        write: bool,
        defer_memberships: bool = False,
    ) -> ApplyOutcome:
        # Resolve secret-handling first so the diff can annotate preserved
        # fields. Deep-copy because `strip_encrypted` mutates nested
        # dicts/lists in place — a shallow `dict(payload)` would let nested
        # mutations leak back into the user-supplied payload.
        write_payload = copy.deepcopy(payload)
        preserved, dropped_undeclared = strip_encrypted(write_payload, spec)
        for path in dropped_undeclared:
            self._warn(
                f"undeclared $encrypted$ at {spec.kind}.{path} dropped — "
                f"declare in spec.secret_paths to silence"
            )
        # Decide which top-level fields can be safely omitted from a PATCH
        # (AWX retains the existing value, including nested secrets) and
        # which would silently clobber a secret if PATCHed. The latter are
        # rejected at the boundary — the user must either provide the real
        # secret value or revert their sibling change.
        preserved_fields, conflict_fields = self._secret_policy.partition(
            write_payload=write_payload,
            existing=existing,
            preserved=preserved,
        )
        if conflict_fields:
            raise BadRequest(
                f"Cannot apply {spec.kind} {resource.metadata.name!r}: "
                f"{', '.join(sorted(conflict_fields))} contain a $encrypted$ placeholder "
                f"alongside a sibling change. PATCH would overwrite the existing secret. "
                f"Provide the actual secret value(s) or revert the sibling change(s)."
            )
        changes = _diff(spec, existing, write_payload, preserved_fields=preserved_fields)

        # Membership reconciliation (multi-FK + sub_endpoint, e.g.
        # ``Group.hosts`` / ``Group.children``). The plan is computed
        # *now* so its diff appears in preview output. When
        # ``defer_memberships=True``, planning is skipped entirely —
        # phase 1 of two-phase apply only writes bodies; the deferred
        # plan + execute happens in :meth:`reconcile_memberships`. This
        # matters when a sibling member is declared in the same file
        # and won't exist until later in phase 1.
        membership_plans: list[_SubEndpointPlan] = []
        if not defer_memberships:
            membership_plans = _plan_sub_endpoints(
                spec,
                resource,
                int(existing["id"]) if existing else None,
                client=self._client,
                fk=self._fk,
            )
            for plan in membership_plans:
                if plan.field_change is not None:
                    changes.append(plan.field_change)

        if not write:
            action = "preview"
            return ApplyOutcome(
                kind=spec.kind,
                name=resource.metadata.name,
                action=action,
                changes=changes,
                preserved_secrets=preserved,
                dropped_undeclared_secrets=dropped_undeclared,
            )

        if existing is None:
            return self._do_create(
                spec=spec,
                resource=resource,
                identity=identity,
                payload=write_payload,
                strategy=strategy,
                changes=changes,
                membership_plans=membership_plans,
                preserved=preserved,
                dropped_undeclared=dropped_undeclared,
            )
        return self._do_update(
            spec=spec,
            resource=resource,
            existing=existing,
            payload=write_payload,
            strategy=strategy,
            changes=changes,
            membership_plans=membership_plans,
            preserved=preserved,
            dropped_undeclared=dropped_undeclared,
        )

    def _do_create(
        self,
        *,
        spec: ResourceSpec,
        resource: Resource,
        identity: dict[str, Any],
        payload: dict[str, Any],
        strategy: ApplyStrategy,
        changes: list[FieldChange],
        membership_plans: list[_SubEndpointPlan],
        preserved: list[str],
        dropped_undeclared: list[str],
    ) -> ApplyOutcome:
        # POSTs cannot use $encrypted$ placeholders — we already stripped
        # declared paths; any *originally-present* placeholder at a declared
        # path is a user error on create.
        if preserved:
            raise BadRequest(
                f"{spec.kind} {resource.metadata.name!r} has placeholder "
                f"secret(s) at {', '.join(preserved)} — provide real values "
                f"or pre-create the resource in AWX first"
            )
        result = strategy.create(spec, payload, identity, client=self._client, fk=self._fk)
        if membership_plans:
            new_id_value = result.get("id") if isinstance(result, dict) else None
            if new_id_value is None:
                # All current strategies populate ``id``. If a future strategy
                # ever returns a body without it (or an opaque non-dict), the
                # resource has been created but membership writes can't
                # target it — fail loudly rather than silently skip.
                raise BadRequest(
                    f"{spec.kind} {resource.metadata.name!r}: create response had no "
                    f"'id'; cannot reconcile membership for "
                    f"{', '.join(p.ref.field for p in membership_plans)}"
                )
            _execute_sub_endpoints(
                spec,
                int(new_id_value),
                membership_plans,
                client=self._client,
            )
        return ApplyOutcome(
            kind=spec.kind,
            name=resource.metadata.name,
            action="created",
            changes=changes,
            preserved_secrets=[],
            dropped_undeclared_secrets=dropped_undeclared,
        )

    def _do_update(
        self,
        *,
        spec: ResourceSpec,
        resource: Resource,
        existing: dict[str, Any],
        payload: dict[str, Any],
        strategy: ApplyStrategy,
        changes: list[FieldChange],
        membership_plans: list[_SubEndpointPlan],
        preserved: list[str],
        dropped_undeclared: list[str],
    ) -> ApplyOutcome:
        # Body fields that actually changed, *excluding* preserved secrets and
        # membership-only field changes (the latter are handled out-of-band
        # via associate / disassociate POSTs, never by PATCHing the body).
        membership_field_names = {p.ref.field for p in membership_plans}
        changed_fields = {
            c.field
            for c in changes
            if c.note != "preserved existing secret" and c.field not in membership_field_names
        }
        membership_changed = any(p.to_associate or p.to_disassociate for p in membership_plans)
        if not changed_fields and not membership_changed:
            return ApplyOutcome(
                kind=spec.kind,
                name=resource.metadata.name,
                action="unchanged",
                changes=changes,
                preserved_secrets=preserved,
                dropped_undeclared_secrets=dropped_undeclared,
            )
        if changed_fields:
            update_payload = {k: v for k, v in payload.items() if k in changed_fields}
            strategy.update(
                spec,
                existing,
                update_payload,
                client=self._client,
                fk=self._fk,
            )
        if membership_changed:
            _execute_sub_endpoints(
                spec,
                int(existing["id"]),
                membership_plans,
                client=self._client,
            )
        return ApplyOutcome(
            kind=spec.kind,
            name=resource.metadata.name,
            action="updated",
            changes=changes,
            preserved_secrets=preserved,
            dropped_undeclared_secrets=dropped_undeclared,
        )


def _build_identity(spec: ResourceSpec, resource: Resource) -> dict[str, Any]:
    """Identity is whichever metadata fields uniquely identify the resource.

    Default: ``{name, organization}``. Schedule includes ``parent``.
    """
    identity: dict[str, Any] = {"name": resource.metadata.name}
    if "organization" in spec.identity_keys:
        identity["organization"] = resource.metadata.organization
    if resource.metadata.parent is not None:
        identity["parent"] = resource.metadata.parent
    return identity


def _build_payload(spec: ResourceSpec, resource: Resource, *, fk: FkResolver) -> dict[str, Any]:
    """Project resource.spec to canonical_fields and resolve FKs."""
    body: dict[str, Any] = {}
    raw = resource.spec
    for field in spec.canonical_fields:
        if field in raw:
            body[field] = raw[field]
    # Inject identity keys from metadata so create payloads include `name`
    # (and `organization` for org-scoped kinds) even when absent from spec.
    for key in spec.identity_keys:
        if key in body:
            continue
        value = getattr(resource.metadata, key, None)
        if value is not None:
            body[key] = value
    # Resolve FKs (skip polymorphic — those live in metadata, not payload —
    # and skip sub_endpoint multi-FKs, which are managed out-of-band via
    # associate / disassociate POSTs against ``/<api_path>/<id>/<sub>/``).
    for ref in spec.fk_refs:
        if ref.polymorphic or ref.field not in body or body[ref.field] is None:
            continue
        if ref.multi and ref.sub_endpoint is not None:
            # Membership goes through ``_execute_sub_endpoints``; never PATCH it.
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

    Centralised so :func:`apply_resource` (apply path) and
    :func:`apply_file._prefetch_plan` (bulk warm-up) read the same
    semantics — otherwise prefetch warms the wrong cache buckets for
    inventory-child kinds (``hosts``/``children``) whose scope lives on
    ``metadata.parent`` rather than in the body.
    """
    if ref.scope_field is None:
        return None
    if ref.scope_field == "organization":
        # For Schedule (and any future kind whose canonical org lives on the
        # polymorphic parent), prefer ``parent.organization`` so name-scoped
        # FK lookups resolve in the parent's org, not the schedule's own
        # (which is typically ``None``).
        org = (
            resource.metadata.parent.organization if resource.metadata.parent else None
        ) or resource.metadata.organization
        if org:
            return {"organization": org}
    if ref.scope_field == "inventory":
        # Hosts and Groups (and their group-membership FKs) are scoped by
        # inventory, not org. The inventory lives on ``metadata.parent``
        # for ``inventory_child`` kinds — there's no separate metadata
        # field for it because Schedule's polymorphic-parent envelope
        # already carries everything we need. When the parent's org is
        # set, also scope by ``inventory__organization`` so AWX
        # disambiguates same-named inventories across orgs (it expands
        # to ``?inventory__name=…&inventory__organization__name=…`` —
        # the only way to disambiguate Host/Group ancestry on AWX's
        # filter surface, since hosts don't carry a direct
        # ``organization`` FK).
        parent = resource.metadata.parent
        if parent is not None and parent.kind == "Inventory":
            scope: dict[str, str] = {"inventory": parent.name}
            if parent.organization:
                scope["inventory__organization"] = parent.organization
            return scope
    return None


def _diff(
    spec: ResourceSpec,
    existing: dict[str, Any] | None,
    desired: dict[str, Any],
    *,
    preserved_fields: set[str],
) -> list[FieldChange]:
    """Return field-level changes between existing and the (stripped) desired payload.

    ``desired`` is the post-strip payload (placeholders removed). Top-level
    fields in ``preserved_fields`` are emitted as ``preserved existing secret``
    rows and are excluded from the PATCH so AWX retains the value (including
    any nested secrets).
    """
    out: list[FieldChange] = []
    if existing is None:
        for field, after in desired.items():
            note = "preserved existing secret" if field in preserved_fields else None
            out.append(FieldChange(field=field, before=None, after=after, note=note))
        return out
    for field, after in desired.items():
        before = existing.get(field)
        if field in preserved_fields:
            out.append(
                FieldChange(
                    field=field,
                    before=before,
                    after=before,  # we keep the existing secret
                    note="preserved existing secret",
                )
            )
            continue
        if not _equal(before, after):
            out.append(FieldChange(field=field, before=before, after=after))
    # Top-level secret fields entirely stripped from ``desired`` (e.g.
    # ``webhook_key``) still need a row so the user sees them in the preview.
    for field in preserved_fields:
        if field in desired:
            continue
        before = existing.get(field)
        out.append(
            FieldChange(
                field=field,
                before=before,
                after=before,
                note="preserved existing secret",
            )
        )
    return out


def _equal(a: Any, b: Any) -> bool:
    """Order-insensitive equality for FK lists (e.g., credentials)."""
    if isinstance(a, list) and isinstance(b, list):
        try:
            return bool(sorted(a, key=repr) == sorted(b, key=repr))
        except TypeError:
            return bool(a == b)
    return bool(a == b)


def _plan_sub_endpoints(
    spec: ResourceSpec,
    resource: Resource,
    record_id: int | None,
    *,
    client: ResourceClient,
    fk: FkResolver,
) -> list[_SubEndpointPlan]:
    """For each ``multi=True, sub_endpoint != None`` FK, compute the plan.

    Reads desired members (a list of names) from ``resource.spec[ref.field]``
    and existing members from ``GET /<api_path>/<id>/<sub_endpoint>/`` (skipped
    when ``record_id is None`` — the resource doesn't exist yet, so existing
    is empty and every desired member becomes an associate).

    A field that's *absent* from the resource spec is left unmanaged: we
    won't wipe membership just because the user didn't list it. An empty
    list (``hosts: []``) explicitly clears membership.
    """
    plans: list[_SubEndpointPlan] = []
    raw = resource.spec if isinstance(resource.spec, dict) else {}
    for ref in spec.fk_refs:
        if not (ref.multi and ref.sub_endpoint and ref.kind):
            continue
        if ref.field not in raw:
            continue
        raw_value = raw[ref.field]
        if not isinstance(raw_value, list):
            # An absent field is unmanaged; an empty list clears membership.
            # A bare string ("hosts: web-01") would otherwise be normalised
            # to [] and silently disassociate every existing member on --yes,
            # which is the most destructive failure mode possible here.
            raise BadRequest(
                f"{spec.kind} {resource.metadata.name!r}: {ref.field!r} must be a "
                f"list of names (got {type(raw_value).__name__}); wrap a single "
                f"value in [ ... ] to clarify intent."
            )
        desired_names = list(raw_value)
        scope = scope_for(ref, resource)
        desired_ids = {fk.name_to_id(ref.kind, str(n), scope=scope) for n in desired_names}

        existing_ids: set[int] = set()
        existing_name_by_id: dict[int, str] = {}
        if record_id is not None:
            for record in client.paginate_sub_endpoint(spec, record_id, ref.sub_endpoint):
                rid = int(record["id"])
                existing_ids.add(rid)
                rname = record.get("name")
                if isinstance(rname, str):
                    existing_name_by_id[rid] = rname

        to_associate = sorted(desired_ids - existing_ids)
        to_disassociate = sorted(existing_ids - desired_ids)

        field_change: FieldChange | None = None
        if to_associate or to_disassociate:
            before = sorted(existing_name_by_id.get(i, str(i)) for i in existing_ids)
            after = sorted(desired_names)
            field_change = FieldChange(field=ref.field, before=before, after=after)

        plans.append(
            _SubEndpointPlan(
                ref=ref,
                to_associate=tuple(to_associate),
                to_disassociate=tuple(to_disassociate),
                field_change=field_change,
            )
        )
    return plans


def _execute_sub_endpoints(
    spec: ResourceSpec,
    record_id: int,
    plans: list[_SubEndpointPlan],
    *,
    client: ResourceClient,
) -> None:
    """POST associate / disassociate per ``plans`` against the resource's id."""
    for plan in plans:
        if plan.ref.sub_endpoint is None:
            continue
        for member_id in plan.to_associate:
            client.sub_endpoint_request(
                spec, record_id, plan.ref.sub_endpoint, "POST", json={"id": member_id}
            )
        for member_id in plan.to_disassociate:
            client.sub_endpoint_request(
                spec,
                record_id,
                plan.ref.sub_endpoint,
                "POST",
                json={"id": member_id, "disassociate": True},
            )


__all__ = ["ApplyResource"]
