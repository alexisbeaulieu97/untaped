"""Reconcile multi-FK ``sub_endpoint`` memberships.

For each ``FkRef(multi=True, sub_endpoint=…)`` on a spec (today: only
``GROUP_SPEC.hosts`` / ``GROUP_SPEC.children``), the reconciler:

1. Reads the desired list of member names from ``resource.spec[ref.field]``.
2. Fetches the existing members from
   ``GET /<api_path>/<id>/<sub_endpoint>/`` (skipped when the resource
   doesn't exist yet — ``record_id is None``).
3. Computes ``to_associate`` (in desired but not existing) and
   ``to_disassociate`` (in existing but not desired).
4. Builds a :class:`FieldChange` row when there's any diff so the
   apply preview stays honest.

Membership writes are *kept out of the PATCH body* — AWX never sees
``hosts: [...]`` on a Group write. Body and membership are independent
write paths.

:func:`member_delta`, :func:`credential_clashes` and :func:`write_members`
are the shared rules for replacing any relationship (template memberships
here, workflow node prompts in ``apply_workflow_graph``): adds first,
same-type credentials removed first, ordered lists rebuilt from their first
difference, and removed members restored when an add is refused.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from untaped.capabilities.awx.application.apply_planner import resolve_fk_value, scope_for
from untaped.capabilities.awx.application.mutation_refs import DeferredReference, PlannedId
from untaped.capabilities.awx.application.ports import Catalog, FkResolver, ResourceClient
from untaped.capabilities.awx.domain import FieldChange, FkRef, Resource, ResourceSpec
from untaped.capabilities.awx.errors import BadRequestError
from untaped.capability_api import attribution

# Exact resource kind, selected controller ID, and relationship field.
type MembershipSnapshots = Mapping[tuple[str, int, str], tuple[dict[str, Any], ...]]


@dataclass(frozen=True)
class MembershipPlan:
    """One reconciled multi-FK relationship.

    ``ref`` describes the spec field (e.g. ``Group.hosts``);
    ``field_change`` is non-None only when ``to_associate`` or
    ``to_disassociate`` is non-empty (so the apply diff stays quiet for
    unmodified memberships).
    """

    ref: FkRef
    to_associate: tuple[PlannedId, ...]
    to_disassociate: tuple[int, ...]
    field_change: FieldChange | None
    existing_ids: tuple[int, ...] = ()
    desired_ids: tuple[PlannedId, ...] = ()
    to_reorder: tuple[PlannedId, ...] = ()
    mode: Literal["replacement", "additive"] = "replacement"
    requested_associate: tuple[int, ...] = ()
    requested_disassociate: tuple[int, ...] = ()
    disassociate_first: tuple[int, ...] = ()
    """Removed members that must leave before associating (credential type clash)."""


class MembershipReconciler:
    """Plan + execute multi-FK sub-endpoint membership writes.

    Replacements associate before they disassociate, so a refused associate
    (for example a 403 on one credential) never strips the resource. The only
    exception is a credential whose type matches an incoming credential: AWX
    allows one credential per type, so that one must leave first. If an
    associate still fails after removals, the removed members are re-added.
    """

    def __init__(self, catalog: Catalog | None = None) -> None:
        self._catalog = catalog

    def plan(  # noqa: C901
        self,
        spec: ResourceSpec,
        resource: Resource,
        record_id: int | None,
        *,
        client: ResourceClient,
        fk: FkResolver,
        membership_snapshots: MembershipSnapshots | None = None,
    ) -> list[MembershipPlan]:
        """For each ``multi=True, sub_endpoint != None`` FK, compute the plan.

        A field that's *absent* from the resource spec is left
        unmanaged: we won't wipe membership just because the user didn't
        list it. An empty list (``hosts: []``) explicitly clears
        membership.
        """
        plans: list[MembershipPlan] = []
        raw = resource.spec if isinstance(resource.spec, dict) else {}
        for ref in spec.fk_refs:
            if not (ref.multi and ref.sub_endpoint and ref.kind):
                continue
            if ref.field not in raw:
                continue
            raw_value = raw[ref.field]
            if not isinstance(raw_value, list):
                # An absent field is unmanaged; an empty list clears
                # membership. A bare string ("hosts: web-01") would
                # otherwise be normalised to [] and silently
                # disassociate every existing member on --yes, which is
                # the most destructive failure mode possible here.
                raise BadRequestError(
                    f"{spec.kind} {resource.metadata.name!r}: {ref.field!r} must be a "
                    f"list of names (got {type(raw_value).__name__}); wrap a single "
                    f"value in [ ... ] to clarify intent"
                )
            desired_names = list(raw_value)
            scope = scope_for(ref, resource)
            existing_ids: list[int] = []
            existing_name_by_id: dict[int, str] = {}
            existing_type_by_id: dict[int, Any] = {}
            if record_id is not None:
                member_records: Iterable[dict[str, Any]]
                if membership_snapshots is None:
                    member_records = client.paginate_sub_endpoint(spec, record_id, ref.sub_endpoint)
                else:
                    key = (spec.kind, record_id, ref.field)
                    if key not in membership_snapshots:
                        raise BadRequestError("missing initial editor membership snapshot")
                    member_records = membership_snapshots[key]
                for record in member_records:
                    rid = int(record["id"])
                    if rid not in existing_ids:
                        existing_ids.append(rid)
                    rname = record.get("name")
                    if isinstance(rname, str):
                        existing_name_by_id[rid] = rname
                    if record.get("credential_type") is not None:
                        existing_type_by_id[rid] = record["credential_type"]

            resolved_desired_ids = tuple(
                resolve_fk_value(ref.kind, value, scope=scope, fk=fk) for value in desired_names
            )
            if len(set(resolved_desired_ids)) != len(resolved_desired_ids):
                raise BadRequestError(
                    f"{spec.kind} {resource.metadata.name!r}: "
                    f"{ref.field!r} contains duplicate members"
                )

            to_associate, to_disassociate, to_reorder = member_delta(
                tuple(existing_ids), resolved_desired_ids, ordered=ref.ordered
            )
            disassociate_first: tuple[int, ...] = ()
            if ref.kind == "Credential" and to_associate and to_disassociate:
                disassociate_first = credential_clashes(
                    to_associate,
                    to_disassociate,
                    existing_type_by_id,
                    lambda member: self.credential_type(member, client),
                )

            field_change: FieldChange | None = None
            if to_associate or to_disassociate:
                before = (
                    [existing_name_by_id.get(i, str(i)) for i in existing_ids]
                    if ref.ordered
                    else sorted(existing_name_by_id.get(i, str(i)) for i in existing_ids)
                )
                after = list(desired_names) if ref.ordered else sorted(desired_names, key=str)
                field_change = FieldChange(field=ref.field, before=before, after=after)
            elif to_reorder:
                before = [existing_name_by_id.get(i, str(i)) for i in existing_ids]
                field_change = FieldChange(
                    field=ref.field,
                    before=before,
                    after=list(desired_names),
                )

            plans.append(
                MembershipPlan(
                    ref=ref,
                    to_associate=to_associate,
                    to_disassociate=to_disassociate,
                    field_change=field_change,
                    existing_ids=tuple(existing_ids),
                    desired_ids=resolved_desired_ids,
                    to_reorder=to_reorder,
                    disassociate_first=disassociate_first,
                )
            )
        return plans

    def credential_type(self, member: PlannedId, client: ResourceClient) -> Any:
        """A credential's type, or ``None`` when it cannot be read (yet)."""
        if self._catalog is None or not isinstance(member, int):
            return None
        try:
            record = client.get(self._catalog.get("Credential"), member)
        except Exception:
            return None
        return record.model_dump().get("credential_type")

    def plan_additive(
        self,
        spec: ResourceSpec,
        record_id: int,
        ref: FkRef,
        member_ids: Iterable[int],
        *,
        disassociate: bool = False,
        client: ResourceClient,
    ) -> MembershipPlan:
        """Capture an explicit add/remove operation without replacing other members."""
        ids = tuple(dict.fromkeys(member_ids))
        if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in ids):
            raise BadRequestError("membership IDs must be positive integers")
        existing = (
            tuple(
                int(item["id"])
                for item in client.paginate_sub_endpoint(spec, record_id, ref.sub_endpoint)
            )
            if ref.sub_endpoint
            else ()
        )
        associate = () if disassociate else tuple(item for item in ids if item not in existing)
        remove = tuple(item for item in ids if item in existing) if disassociate else ()
        return MembershipPlan(
            ref=ref,
            to_associate=associate,
            to_disassociate=remove,
            existing_ids=existing,
            requested_associate=() if disassociate else ids,
            requested_disassociate=ids if disassociate else (),
            field_change=FieldChange(
                field=ref.field,
                before=list(existing),
                after=[item for item in existing if item not in remove] + list(associate),
            )
            if associate or remove
            else None,
            mode="additive",
        )

    def verify(
        self,
        spec: ResourceSpec,
        record_id: int,
        plans: Iterable[MembershipPlan],
        *,
        client: ResourceClient,
    ) -> None:
        """Read back replacements exactly, or additive requested presence/absence."""
        for plan in plans:
            if plan.ref.sub_endpoint is None:
                continue
            observed = tuple(
                int(item["id"])
                for item in client.paginate_sub_endpoint(spec, record_id, plan.ref.sub_endpoint)
            )
            if plan.mode == "additive":
                matches = set(plan.requested_associate).issubset(observed) and not set(
                    plan.requested_disassociate
                ).intersection(observed)
            elif plan.ref.ordered:
                matches = observed == plan.desired_ids
            else:
                matches = set(observed) == set(plan.desired_ids)
            if not matches:
                raise BadRequestError(
                    f"{spec.kind}#{record_id}: membership {plan.ref.field!r} did not converge"
                )

    def execute(
        self,
        spec: ResourceSpec,
        record_id: int,
        plans: list[MembershipPlan],
        *,
        client: ResourceClient,
    ) -> None:
        """POST associate / disassociate per ``plans`` against the resource's id."""
        for plan in plans:

            def post(member_id: PlannedId, disassociate: bool, plan: MembershipPlan = plan) -> None:
                self.post_members(
                    spec,
                    parent_id=record_id,
                    ref=plan.ref,
                    member_ids=(member_id,),
                    disassociate=disassociate,
                    client=client,
                )

            write_members(
                post,
                field=plan.ref.field,
                associate=plan.to_associate,
                disassociate=plan.to_disassociate,
                desired=plan.desired_ids,
                reorder=plan.to_reorder,
                ordered=plan.ref.ordered and plan.mode == "replacement",
                disassociate_first=plan.disassociate_first,
            )

    def post_members(
        self,
        spec: ResourceSpec,
        *,
        parent_id: int,
        ref: FkRef,
        member_ids: Iterable[PlannedId],
        disassociate: bool = False,
        client: ResourceClient,
    ) -> None:
        """Issue associate (or disassociate) sub-endpoint POSTs without a diff.

        AWX's sub-endpoint POSTs are idempotent — re-adding or
        re-removing a member returns a 2xx without changing state — so
        callers can rely on additive semantics. Used by :meth:`execute`
        for both directions of a diff-driven reconciliation and
        directly by
        :class:`untaped.capabilities.awx.application.manage_membership.ManageMembership`
        for the additive ``<parent> <sub_endpoint> add/remove`` CLI flow.

        ``ref.sub_endpoint is None`` short-circuits before iterating
        ``member_ids``. :class:`MembershipPlan` and :class:`FkRef` are
        both public — a future external caller could build a plan with
        ``FkRef(sub_endpoint=None)`` and reach this method through
        :meth:`execute`; the guard ensures no malformed POST escapes.
        """
        if ref.sub_endpoint is None:
            return
        for member_id in member_ids:
            if isinstance(member_id, DeferredReference):
                raise BadRequestError(
                    f"membership reference {member_id.kind} {member_id.name!r} "
                    "was not bound before execution"
                )
            body: dict[str, Any] = {"id": member_id}
            if disassociate:
                body["disassociate"] = True
            client.sub_endpoint_request(spec, parent_id, ref.sub_endpoint, "POST", json=body)


def member_delta(
    existing: tuple[int, ...], desired: tuple[PlannedId, ...], *, ordered: bool
) -> tuple[tuple[PlannedId, ...], tuple[int, ...], tuple[PlannedId, ...]]:
    """``(associate, disassociate, reorder)`` turning ``existing`` into ``desired``."""
    existing_set, desired_set = set(existing), set(desired)
    associate = tuple(member for member in desired if member not in existing_set)
    disassociate = tuple(member for member in existing if member not in desired_set)
    reorder: tuple[PlannedId, ...] = ()
    if ordered:
        reorder = _ordered_replacements(
            existing_ids=existing, desired_ids=desired, removed=set(disassociate)
        )
    return associate, disassociate, reorder


def credential_clashes(
    associate: Sequence[PlannedId],
    disassociate: tuple[int, ...],
    existing_types: Mapping[int, Any],
    type_of: Callable[[PlannedId], Any],
) -> tuple[int, ...]:
    """Removed credentials sharing a type with an incoming one (AWX allows one per type).

    An unknown type on either side is treated as a clash, so a same-type
    swap still frees the slot; the restore on failure covers that case.
    """
    incoming: set[Any] = set()
    for member in associate:
        member_type = type_of(member)
        if member_type is None:
            return disassociate
        incoming.add(member_type)
    return tuple(
        member
        for member in disassociate
        if existing_types.get(member) is None or existing_types[member] in incoming
    )


def write_members(
    post: Callable[[PlannedId, bool], None],
    *,
    field: str,
    associate: Sequence[PlannedId],
    disassociate: Sequence[int],
    desired: Sequence[PlannedId] = (),
    reorder: Sequence[PlannedId] = (),
    ordered: bool = False,
    disassociate_first: Sequence[int] = (),
) -> None:
    """Write one relationship change through ``post(member, disassociate)``.

    Adds come first so a refused add never strips the resource; only
    ``disassociate_first`` members (same-type credentials) leave beforehand.
    An ordered list removes its ``reorder`` tail and re-adds it in ``desired``
    order. If an add fails after removals, the removed members are re-added.
    """
    operations: tuple[tuple[tuple[PlannedId, ...], bool], ...]
    if ordered:
        adding = set(reorder) | set(associate)
        operations = (
            (tuple(reorder), True),
            (tuple(member for member in desired if member in adding), False),
            (tuple(m for m in disassociate if m not in reorder), True),
        )
    else:
        operations = (
            (tuple(disassociate_first), True),
            (tuple(associate), False),
            (tuple(m for m in disassociate if m not in disassociate_first), True),
        )
    removed: list[PlannedId] = []
    for member_ids, removing in operations:
        try:
            for member_id in member_ids:
                post(member_id, removing)
                if removing:
                    removed.append(member_id)
        except Exception as exc:
            if removing or not removed:
                raise
            raise _restore(post, field, removed, exc) from exc


def _restore(
    post: Callable[[PlannedId, bool], None],
    field: str,
    removed: list[PlannedId],
    error: Exception,
) -> BadRequestError:
    """Re-add members removed earlier in this reconcile after an add failed."""
    lost: list[PlannedId] = []
    for member_id in removed:
        try:
            post(member_id, False)
        except Exception:
            lost.append(member_id)
    members = ", ".join(str(member) for member in lost or removed)
    outcome = (
        f"could not restore removed members {members}"
        if lost
        else f"restored removed members {members}"
    )
    return BadRequestError(f"{field}: associate failed ({error}); {outcome}", **attribution(error))


def _ordered_replacements(
    *,
    existing_ids: tuple[int, ...],
    desired_ids: tuple[PlannedId, ...],
    removed: set[int],
) -> tuple[int, ...]:
    """Return the desired tail that must be removed and re-added.

    AWX's ordered-many-to-many endpoint appends an association and does not
    move an existing through-row.  Keep the longest already-valid prefix and
    re-add only the remaining desired members.  Unrelated members are removed
    separately by ``to_disassociate``.
    """
    current = [member_id for member_id in existing_ids if member_id not in removed]
    prefix = 0
    while prefix < len(current) and prefix < len(desired_ids):
        if current[prefix] != desired_ids[prefix]:
            break
        prefix += 1
    if current == list(desired_ids):
        return ()
    desired_tail = desired_ids[prefix:]
    current_set = set(current)
    return tuple(member_id for member_id in desired_tail if member_id in current_set)
