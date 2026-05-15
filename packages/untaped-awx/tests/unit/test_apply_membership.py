"""Unit tests for MembershipReconciler.

Plans + executes the multi-FK sub-endpoint reconciliation that backs
``Group.hosts`` / ``Group.children`` (and any future kind that declares
``FkRef(multi=True, sub_endpoint=…)``). Pure orchestration around a
``ResourceClient`` + ``FkResolver`` — focused stubs for both.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest
from untaped_awx.application.apply_membership import MembershipPlan, MembershipReconciler
from untaped_awx.application.ports import FkResolver, ResourceClient
from untaped_awx.domain import FieldChange, FkRef, Metadata, Resource, ResourceSpec
from untaped_awx.domain.envelope import IdentityRef
from untaped_awx.errors import BadRequest
from untaped_awx.infrastructure.specs import GROUP_SPEC, JOB_TEMPLATE_SPEC


class _StubClient:
    def __init__(self, *, existing_members: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self._existing = existing_members or {}
        self.subendpoint_calls: list[tuple[int, str, str, dict[str, Any] | None]] = []

    def paginate_sub_endpoint(
        self,
        spec: ResourceSpec,
        record_id: int,
        sub_endpoint: str,
        *,
        params: dict[str, str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        return iter(self._existing.get(sub_endpoint, []))

    def sub_endpoint_request(
        self,
        spec: ResourceSpec,
        record_id: int,
        sub_endpoint: str,
        method: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.subendpoint_calls.append((record_id, sub_endpoint, method, json))
        return {}


class _StubFk:
    def __init__(self, names: dict[tuple[str, str], int]) -> None:
        self._names = names

    def name_to_id(self, kind: str, name: str, *, scope: dict[str, str] | None = None) -> int:
        return self._names[(kind, name)]

    def id_to_name(self, kind: str, id_: int) -> str:
        for (k, n), i in self._names.items():
            if k == kind and i == id_:
                return n
        raise KeyError((kind, id_))

    def resolve_polymorphic(self, value: dict[str, Any]) -> tuple[str, int]:
        raise NotImplementedError

    def prefetch(self, plan: dict[str, list[dict[str, str] | None]]) -> None:
        return None


def _group(
    name: str, *, hosts: list[str] | None = None, children: list[str] | None = None
) -> Resource:
    spec: dict[str, Any] = {}
    if hosts is not None:
        spec["hosts"] = hosts
    if children is not None:
        spec["children"] = children
    return Resource(
        kind="Group",
        metadata=Metadata(
            name=name,
            parent=IdentityRef(kind="Inventory", name="prod", organization="Default"),
        ),
        spec=spec,
    )


# ---- plan ----


def test_plan_returns_empty_for_kinds_without_sub_endpoint_refs() -> None:
    """JobTemplate has no FkRefs with multi=True + sub_endpoint, so the
    reconciler has no work."""
    rec = MembershipReconciler()
    plans = rec.plan(
        JOB_TEMPLATE_SPEC,
        Resource(kind="JobTemplate", metadata=Metadata(name="t"), spec={}),
        record_id=1,
        client=cast(ResourceClient, _StubClient()),
        fk=cast(FkResolver, _StubFk({})),
    )
    assert plans == []


def test_plan_skips_field_absent_from_resource_spec() -> None:
    """Absent membership field is *unmanaged* — we do not wipe."""
    rec = MembershipReconciler()
    plans = rec.plan(
        GROUP_SPEC,
        _group("g1"),  # no hosts:, no children:
        record_id=42,
        client=cast(
            ResourceClient,
            _StubClient(existing_members={"hosts": [{"id": 7, "name": "web-01"}]}),
        ),
        fk=cast(FkResolver, _StubFk({})),
    )
    assert plans == []


def test_plan_record_id_none_treats_existing_as_empty() -> None:
    """When the resource doesn't exist yet, every desired member becomes
    an associate — no existing-member fetch is issued."""
    rec = MembershipReconciler()
    plans = rec.plan(
        GROUP_SPEC,
        _group("g1", hosts=["web-01", "web-02"]),
        record_id=None,
        client=cast(ResourceClient, _StubClient()),
        fk=cast(FkResolver, _StubFk({("Host", "web-01"): 7, ("Host", "web-02"): 8})),
    )
    hosts_plan = next(p for p in plans if p.ref.field == "hosts")
    assert sorted(hosts_plan.to_associate) == [7, 8]
    assert hosts_plan.to_disassociate == ()


def test_plan_associate_only() -> None:
    rec = MembershipReconciler()
    plans = rec.plan(
        GROUP_SPEC,
        _group("g1", hosts=["web-01", "web-02"]),
        record_id=42,
        client=cast(
            ResourceClient,
            _StubClient(existing_members={"hosts": [{"id": 7, "name": "web-01"}]}),
        ),
        fk=cast(FkResolver, _StubFk({("Host", "web-01"): 7, ("Host", "web-02"): 8})),
    )
    hosts_plan = next(p for p in plans if p.ref.field == "hosts")
    assert hosts_plan.to_associate == (8,)
    assert hosts_plan.to_disassociate == ()


def test_plan_disassociate_only() -> None:
    rec = MembershipReconciler()
    client = _StubClient(
        existing_members={"hosts": [{"id": 7, "name": "web-01"}, {"id": 8, "name": "web-02"}]}
    )
    plans = rec.plan(
        GROUP_SPEC,
        _group("g1", hosts=["web-01"]),
        record_id=42,
        client=cast(ResourceClient, client),
        fk=cast(FkResolver, _StubFk({("Host", "web-01"): 7})),
    )
    hosts_plan = next(p for p in plans if p.ref.field == "hosts")
    assert hosts_plan.to_associate == ()
    assert hosts_plan.to_disassociate == (8,)


def test_plan_mixed_associate_disassociate() -> None:
    rec = MembershipReconciler()
    client = _StubClient(
        existing_members={"hosts": [{"id": 7, "name": "web-01"}, {"id": 8, "name": "web-02"}]}
    )
    plans = rec.plan(
        GROUP_SPEC,
        _group("g1", hosts=["web-01", "web-03"]),
        record_id=42,
        client=cast(ResourceClient, client),
        fk=cast(FkResolver, _StubFk({("Host", "web-01"): 7, ("Host", "web-03"): 9})),
    )
    hosts_plan = next(p for p in plans if p.ref.field == "hosts")
    assert hosts_plan.to_associate == (9,)
    assert hosts_plan.to_disassociate == (8,)


def test_plan_empty_list_clears_membership() -> None:
    """``hosts: []`` is the explicit "remove every host" gesture."""
    rec = MembershipReconciler()
    client = _StubClient(
        existing_members={"hosts": [{"id": 7, "name": "web-01"}, {"id": 8, "name": "web-02"}]}
    )
    plans = rec.plan(
        GROUP_SPEC,
        _group("g1", hosts=[]),
        record_id=42,
        client=cast(ResourceClient, client),
        fk=cast(FkResolver, _StubFk({})),
    )
    hosts_plan = next(p for p in plans if p.ref.field == "hosts")
    assert hosts_plan.to_associate == ()
    assert sorted(hosts_plan.to_disassociate) == [7, 8]


def test_plan_rejects_non_list_field() -> None:
    """A bare string for ``hosts:`` would be silently coerced to ``[]``
    and disassociate every member — most destructive footgun. Reject."""
    rec = MembershipReconciler()
    with pytest.raises(BadRequest, match="must be a list of names"):
        rec.plan(
            GROUP_SPEC,
            Resource(
                kind="Group",
                metadata=Metadata(
                    name="g1",
                    parent=IdentityRef(kind="Inventory", name="prod", organization="Default"),
                ),
                spec={"hosts": "web-01"},  # bare string, not list
            ),
            record_id=42,
            client=cast(ResourceClient, _StubClient()),
            fk=cast(FkResolver, _StubFk({})),
        )


def test_plan_field_change_is_none_when_no_changes() -> None:
    """Identical desired vs existing means no associate, no disassociate,
    so the diff stays quiet (no ``FieldChange`` row)."""
    rec = MembershipReconciler()
    plans = rec.plan(
        GROUP_SPEC,
        _group("g1", hosts=["web-01"]),
        record_id=42,
        client=cast(
            ResourceClient,
            _StubClient(existing_members={"hosts": [{"id": 7, "name": "web-01"}]}),
        ),
        fk=cast(FkResolver, _StubFk({("Host", "web-01"): 7})),
    )
    hosts_plan = next(p for p in plans if p.ref.field == "hosts")
    assert hosts_plan.field_change is None


def test_plan_field_change_carries_sorted_before_after() -> None:
    rec = MembershipReconciler()
    plans = rec.plan(
        GROUP_SPEC,
        _group("g1", hosts=["web-02", "web-01"]),
        record_id=42,
        client=cast(
            ResourceClient,
            _StubClient(existing_members={"hosts": [{"id": 9, "name": "web-03"}]}),
        ),
        fk=cast(FkResolver, _StubFk({("Host", "web-01"): 7, ("Host", "web-02"): 8})),
    )
    hosts_plan = next(p for p in plans if p.ref.field == "hosts")
    assert hosts_plan.field_change is not None
    assert hosts_plan.field_change.field == "hosts"
    assert hosts_plan.field_change.before == ["web-03"]
    assert hosts_plan.field_change.after == ["web-01", "web-02"]


# ---- execute ----


def test_execute_issues_associate_then_disassociate_posts() -> None:
    rec = MembershipReconciler()
    client = _StubClient()
    plans = [
        MembershipPlan(
            ref=GROUP_SPEC.fk_refs[0],  # hosts
            to_associate=(7, 8),
            to_disassociate=(9,),
            field_change=FieldChange(field="hosts", before=[], after=[]),
        )
    ]
    rec.execute(GROUP_SPEC, 42, plans, client=cast(ResourceClient, client))
    assert client.subendpoint_calls == [
        (42, "hosts", "POST", {"id": 7}),
        (42, "hosts", "POST", {"id": 8}),
        (42, "hosts", "POST", {"id": 9, "disassociate": True}),
    ]


def test_execute_skips_plans_with_no_work() -> None:
    rec = MembershipReconciler()
    client = _StubClient()
    plans = [
        MembershipPlan(
            ref=GROUP_SPEC.fk_refs[0],
            to_associate=(),
            to_disassociate=(),
            field_change=None,
        )
    ]
    rec.execute(GROUP_SPEC, 42, plans, client=cast(ResourceClient, client))
    assert client.subendpoint_calls == []


# ---- post_members ----


def _hosts_ref() -> Any:
    return next(r for r in GROUP_SPEC.fk_refs if r.field == "hosts")


def test_post_members_associates_each_id() -> None:
    rec = MembershipReconciler()
    client = _StubClient()
    rec.post_members(
        GROUP_SPEC,
        parent_id=42,
        ref=_hosts_ref(),
        member_ids=[7, 8],
        client=cast(ResourceClient, client),
    )
    assert client.subendpoint_calls == [
        (42, "hosts", "POST", {"id": 7}),
        (42, "hosts", "POST", {"id": 8}),
    ]


def test_post_members_disassociate_sets_flag() -> None:
    rec = MembershipReconciler()
    client = _StubClient()
    rec.post_members(
        GROUP_SPEC,
        parent_id=42,
        ref=_hosts_ref(),
        member_ids=[7],
        disassociate=True,
        client=cast(ResourceClient, client),
    )
    assert client.subendpoint_calls == [
        (42, "hosts", "POST", {"id": 7, "disassociate": True}),
    ]


def test_post_members_empty_ids_is_a_noop() -> None:
    rec = MembershipReconciler()
    client = _StubClient()
    rec.post_members(
        GROUP_SPEC,
        parent_id=42,
        ref=_hosts_ref(),
        member_ids=[],
        client=cast(ResourceClient, client),
    )
    assert client.subendpoint_calls == []


def test_post_members_ref_without_sub_endpoint_is_a_noop() -> None:
    """Defensive: a ``FkRef(sub_endpoint=None)`` (the dataclass is public —
    a future external caller could build one) must short-circuit before
    iterating ``member_ids`` so no malformed POST is ever issued."""
    rec = MembershipReconciler()
    client = _StubClient()
    rec.post_members(
        GROUP_SPEC,
        parent_id=42,
        ref=FkRef(field="hosts", kind="Host"),  # sub_endpoint default = None
        member_ids=[7, 8],
        client=cast(ResourceClient, client),
    )
    assert client.subendpoint_calls == []
