"""Use case: upsert a single :class:`Resource` doc against AWX.

Default behaviour is **preview** — the diff is computed and returned
without writing. Pass ``write=True`` (CLI: ``--yes``) to actually
PATCH/POST. The caller decides whether to gate writes on a confirmation
prompt.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from untaped.capabilities.awx.application._secret_paths import strip_encrypted_in_place
from untaped.capabilities.awx.application.apply_field_diff import PRESERVED_SECRET_NOTE, FieldDiff
from untaped.capabilities.awx.application.apply_membership import (
    MembershipPlan,
    MembershipReconciler,
)
from untaped.capabilities.awx.application.apply_planner import ApplyPlanner, unrecognized_warning
from untaped.capabilities.awx.application.apply_secret_policy import SecretPreservationPolicy
from untaped.capabilities.awx.application.apply_verifier import ApplyVerifier
from untaped.capabilities.awx.application.ports import (
    ApplyStrategy,
    Catalog,
    FkResolver,
    RawHttpResourceClient,
    StrategyResolver,
)
from untaped.capabilities.awx.domain import (
    ApplyOutcome,
    FieldChange,
    Resource,
    ResourceSpec,
)
from untaped.capabilities.awx.errors import AwxApiError, BadRequest

WarnFn = Callable[[str], None]


def _noop_warn(_msg: str) -> None: ...


def _scope_from_identity(identity: dict[str, Any]) -> dict[str, Any]:
    """Return the user-facing scope slice of a prepared identity."""
    return {key: value for key, value in identity.items() if key not in {"name", "id"}}


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
        field_diff: FieldDiff | None = None,
        membership: MembershipReconciler | None = None,
        planner: ApplyPlanner | None = None,
        verifier: ApplyVerifier | None = None,
        allow_unverified: bool = False,
    ) -> None:
        self._client = client
        self._catalog = catalog
        self._fk = fk
        self._strategies = strategies
        self._warn = warn
        self._secret_policy = secret_policy or SecretPreservationPolicy()
        self._field_diff = field_diff or FieldDiff()
        self._membership = membership or MembershipReconciler()
        self._planner = planner or ApplyPlanner()
        self._verifier = verifier or ApplyVerifier(secret_policy=self._secret_policy)
        self._allow_unverified = allow_unverified

    def __call__(
        self,
        resource: Resource,
        *,
        write: bool = False,
        defer_memberships: bool = False,
    ) -> ApplyOutcome:
        """Apply ``resource`` (preview by default; pass ``write=True`` to write).

        ``defer_memberships=True`` (only meaningful when ``write=True``)
        writes the body but skips :meth:`MembershipReconciler.execute`.
        Used by :class:`ApplyFile` to break cyclic membership dependencies — a
        Group whose ``children:`` references a sibling Group declared
        later in the same file would otherwise fail in phase 1 because
        the sibling doesn't exist yet. After every doc's body has been
        written, :class:`ApplyFile` calls :meth:`reconcile_memberships`
        to drive the deferred writes against now-existing siblings.
        """
        spec, identity, payload, strategy = self._prepare(resource)
        self._warn_unrecognized(spec, resource)
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

    def apply_to_existing(
        self,
        resource: Resource,
        existing: dict[str, Any] | None,
        *,
        write: bool = False,
        defer_memberships: bool = False,
        preserve_existing_fk_ids: bool = False,
    ) -> ApplyOutcome:
        """Apply ``resource``'s fields against an already-resolved record.

        The ``apply --stdin`` mass-patch path resolves its selection (by name or
        id) up front and hands the fetched record here, so we skip
        ``find_existing``. Because ``existing`` is always a real record this can
        only ever **update** — it never creates — which is exactly what the
        selection path needs (you patch items you listed, never declare new
        ones). Reuses the same diff / secret-preservation / FK / membership
        logic as :meth:`__call__`.
        """
        spec, identity, payload, strategy = self._prepare(
            resource,
            existing=existing,
            preserve_existing_fk_ids=preserve_existing_fk_ids,
        )
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

    def _warn_unrecognized(self, spec: ResourceSpec, resource: Resource) -> None:
        """Warn once (file-mode, per doc) about fields not in ``spec``'s schema.

        Passthrough still sends them; this only keeps them visible. The
        ``apply --stdin`` seam (:meth:`apply_to_existing`) deliberately does not
        call this — its caller warns once over the shared overlay instead of
        once per resolved item.
        """
        message = unrecognized_warning(spec, resource.spec.keys())
        if message is not None:
            self._warn(message)

    def _prepare(
        self,
        resource: Resource,
        *,
        existing: dict[str, Any] | None = None,
        preserve_existing_fk_ids: bool = False,
    ) -> tuple[ResourceSpec, dict[str, Any], dict[str, Any], ApplyStrategy]:
        """Resolve spec, identity, planned payload, and strategy for a doc.

        Shared by :meth:`__call__` and :meth:`apply_to_existing`. Rejects
        ``read_only`` kinds (Credential, Inventory, Organization,
        CredentialType, plus the catalog-only stubs
        ExecutionEnvironment/Label/InstanceGroup) at the boundary — per-kind
        sub-apps already hide ``apply``, but the top-level ``untaped awx apply
        <file>`` reaches this use case directly via ``apply_file`` and would
        otherwise issue create/update calls for resources whose CRUD is
        deferred.
        """
        spec = self._catalog.get(resource.kind)
        if spec.fidelity == "read_only":
            raise BadRequest(
                f"{spec.kind} does not support apply (fidelity={spec.fidelity!r}); "
                "edit this resource via the AWX UI or API directly."
            )
        identity = self._planner.plan_identity(spec, resource)
        payload = self._planner.plan_payload(
            spec,
            resource,
            fk=self._fk,
            existing=existing,
            preserve_existing_fk_ids=preserve_existing_fk_ids,
        )
        strategy = self._strategies.get(spec.apply_strategy)
        return spec, identity, payload, strategy

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
        # Short-circuit kinds without sub-endpoint refs. Saves an extra
        # find_existing call per doc that has no membership state.
        if not any(ref.multi and ref.sub_endpoint for ref in spec.fk_refs):
            return []
        identity = self._planner.plan_identity(spec, resource)
        strategy = self._strategies.get(spec.apply_strategy)
        existing = strategy.find_existing(spec, identity, client=self._client, fk=self._fk)
        if existing is None:
            raise BadRequest(
                f"{spec.kind} {resource.metadata.name!r}: cannot reconcile "
                f"memberships — record vanished between body write and membership pass"
            )
        membership_plans = self._membership.plan(
            spec,
            resource,
            int(existing["id"]),
            client=self._client,
            fk=self._fk,
        )
        if any(p.to_associate or p.to_disassociate for p in membership_plans):
            self._membership.execute(
                spec, int(existing["id"]), membership_plans, client=self._client
            )
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
        # fields. Deep-copy because `strip_encrypted_in_place` mutates
        # nested dicts/lists — a shallow `dict(payload)` would let nested
        # mutations leak back into the user-supplied payload.
        write_payload = copy.deepcopy(payload)
        preserved, dropped_undeclared = strip_encrypted_in_place(write_payload, spec)
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
        changes = self._field_diff.compute(
            existing=existing,
            desired=write_payload,
            preserved_fields=preserved_fields,
        )

        # Membership reconciliation (multi-FK + sub_endpoint, e.g.
        # ``Group.hosts`` / ``Group.children`` / ``JobTemplate.credentials``).
        # The plan is computed
        # *now* so its diff appears in preview output. When
        # ``defer_memberships=True``, planning is skipped entirely —
        # phase 1 of two-phase apply only writes bodies; the deferred
        # plan + execute happens in :meth:`reconcile_memberships`. This
        # matters when a sibling member is declared in the same file
        # and won't exist until later in phase 1.
        membership_plans: list[MembershipPlan] = []
        if not defer_memberships:
            membership_plans = self._membership.plan(
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
                id=int(existing["id"]) if existing is not None and "id" in existing else None,
                identity=identity,
                scope=_scope_from_identity(identity),
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
        membership_plans: list[MembershipPlan],
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
        result_id = result.get("id") if isinstance(result, dict) else None
        new_id = int(result_id) if result_id is not None else None
        if membership_plans and result_id is None:
            # All current strategies populate ``id``. If a future strategy
            # ever returns a body without it (or an opaque non-dict), the
            # resource has been created but membership writes can't
            # target it — fail loudly rather than silently skip.
            raise BadRequest(
                f"{spec.kind} {resource.metadata.name!r}: create response had no "
                f"'id'; cannot reconcile membership for "
                f"{', '.join(p.ref.field for p in membership_plans)}"
            )
        # Verify body convergence before membership writes. A strict failure
        # may leave a created resource without memberships, which is safer than
        # attaching memberships to an unverified body.
        detail = self._verify_payload(
            spec=spec,
            resource=resource,
            payload=payload,
            sent_fields=tuple(payload),
            write_response=result if isinstance(result, dict) else {},
            record_id=new_id,
        )
        if membership_plans:
            assert new_id is not None
            self._membership.execute(
                spec,
                new_id,
                membership_plans,
                client=self._client,
            )
        return ApplyOutcome(
            kind=spec.kind,
            name=resource.metadata.name,
            action="created",
            id=new_id,
            identity=identity,
            scope=_scope_from_identity(identity),
            changes=changes,
            preserved_secrets=[],
            dropped_undeclared_secrets=dropped_undeclared,
            detail=detail,
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
        membership_plans: list[MembershipPlan],
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
            if c.note != PRESERVED_SECRET_NOTE and c.field not in membership_field_names
        }
        membership_changed = any(p.to_associate or p.to_disassociate for p in membership_plans)
        if not changed_fields and not membership_changed:
            return ApplyOutcome(
                kind=spec.kind,
                name=resource.metadata.name,
                action="unchanged",
                id=int(existing["id"]),
                identity=self._planner.plan_identity(spec, resource),
                scope=_scope_from_identity(self._planner.plan_identity(spec, resource)),
                changes=changes,
                preserved_secrets=preserved,
                dropped_undeclared_secrets=dropped_undeclared,
            )
        if changed_fields:
            update_payload = {k: v for k, v in payload.items() if k in changed_fields}
            result = strategy.update(
                spec,
                existing,
                update_payload,
                client=self._client,
                fk=self._fk,
            )
            detail = self._verify_payload(
                spec=spec,
                resource=resource,
                payload=update_payload,
                sent_fields=tuple(update_payload),
                write_response=result if isinstance(result, dict) else {},
                record_id=int(existing["id"]),
            )
        else:
            detail = None
        if membership_changed:
            self._membership.execute(
                spec,
                int(existing["id"]),
                membership_plans,
                client=self._client,
            )
        return ApplyOutcome(
            kind=spec.kind,
            name=resource.metadata.name,
            action="updated",
            id=int(existing["id"]),
            identity=self._planner.plan_identity(spec, resource),
            scope=_scope_from_identity(self._planner.plan_identity(spec, resource)),
            changes=changes,
            preserved_secrets=preserved,
            dropped_undeclared_secrets=dropped_undeclared,
            detail=detail,
        )

    def _verify_payload(
        self,
        *,
        spec: ResourceSpec,
        resource: Resource,
        payload: dict[str, Any],
        sent_fields: tuple[str, ...],
        write_response: dict[str, Any],
        record_id: int | None,
    ) -> str | None:
        """Verify that AWX reflected the fields sent in the body."""
        unreflected = self._verifier.unreflected_fields(
            spec,
            payload,
            write_response,
            fields=sent_fields,
        )
        fallback_error: AwxApiError | None = None
        if unreflected and record_id is not None:
            try:
                after = self._client.get(spec, record_id).model_dump()
            except AwxApiError as exc:
                fallback_error = exc
            else:
                unreflected = self._verifier.unreflected_fields(
                    spec,
                    payload,
                    after,
                    fields=sent_fields,
                )
        if not unreflected:
            return None
        detail = (
            "unverified field(s): "
            f"{', '.join(sorted(unreflected))}; requested state was not reflected by AWX"
        )
        if fallback_error is not None:
            detail = f"{detail}; fallback GET failed"
        if self._allow_unverified:
            self._warn(
                f"{spec.kind} {resource.metadata.name!r}: {detail}; "
                "continuing because --allow-unverified was set"
            )
            return detail
        message = (
            f"{spec.kind} {resource.metadata.name!r}: {detail}. "
            "Use --allow-unverified with --yes to keep an accepted-but-unproven write."
        )
        if fallback_error is not None:
            raise BadRequest(message) from fallback_error
        raise BadRequest(message)


__all__ = ["ApplyResource"]
