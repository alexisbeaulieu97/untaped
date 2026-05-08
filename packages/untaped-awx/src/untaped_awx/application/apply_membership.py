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
"""

from __future__ import annotations

from dataclasses import dataclass

from untaped_awx.application.apply_planner import scope_for
from untaped_awx.application.ports import FkResolver, ResourceClient
from untaped_awx.domain import FieldChange, FkRef, Resource, ResourceSpec
from untaped_awx.errors import BadRequest


@dataclass(frozen=True)
class MembershipPlan:
    """One reconciled multi-FK relationship.

    ``ref`` describes the spec field (e.g. ``Group.hosts``);
    ``field_change`` is non-None only when ``to_associate`` or
    ``to_disassociate`` is non-empty (so the apply diff stays quiet for
    unmodified memberships).
    """

    ref: FkRef
    to_associate: tuple[int, ...]
    to_disassociate: tuple[int, ...]
    field_change: FieldChange | None


class MembershipReconciler:
    """Plan + execute multi-FK sub-endpoint membership writes."""

    def plan(
        self,
        spec: ResourceSpec,
        resource: Resource,
        record_id: int | None,
        *,
        client: ResourceClient,
        fk: FkResolver,
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
                MembershipPlan(
                    ref=ref,
                    to_associate=tuple(to_associate),
                    to_disassociate=tuple(to_disassociate),
                    field_change=field_change,
                )
            )
        return plans

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
            # Defensive: ``plan()`` only emits MembershipPlan entries
            # whose ``ref.sub_endpoint`` is truthy, but ``MembershipPlan``
            # is a public dataclass — a future external caller could
            # build one with a ``FkRef(sub_endpoint=None)``.
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
