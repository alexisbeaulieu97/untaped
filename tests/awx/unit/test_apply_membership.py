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

from untaped.capabilities.awx.application.apply_membership import (
    MembershipPlan,
    MembershipReconciler,
)
from untaped.capabilities.awx.application.ports import FkResolver, ResourceClient
from untaped.capabilities.awx.domain import FieldChange, FkRef, Metadata, Resource, ResourceSpec
from untaped.capabilities.awx.domain.envelope import IdentityRef
from untaped.capabilities.awx.errors import BadRequestError
from untaped.capabilities.awx.infrastructure.specs import (
    GROUP_SPEC,
    JOB_TEMPLATE_SPEC,
    PROJECT_SPEC,
)


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
    """Kinds with no FkRefs with multi=True + sub_endpoint have no work."""
    rec = MembershipReconciler()
    plans = rec.plan(
        PROJECT_SPEC,
        Resource(kind="Project", metadata=Metadata(name="p"), spec={}),
        record_id=1,
        client=cast(ResourceClient, _StubClient()),
        fk=cast(FkResolver, _StubFk({})),
    )
    assert plans == []


def test_plan_reconciles_job_template_credentials() -> None:
    rec = MembershipReconciler()
    plans = rec.plan(
        JOB_TEMPLATE_SPEC,
        Resource(
            kind="JobTemplate",
            metadata=Metadata(name="deploy", organization="Default"),
            spec={"credentials": ["ssh", "vault"]},
        ),
        record_id=42,
        client=cast(
            ResourceClient,
            _StubClient(existing_members={"credentials": [{"id": 30, "name": "ssh"}]}),
        ),
        fk=cast(FkResolver, _StubFk({("Credential", "ssh"): 30, ("Credential", "vault"): 31})),
    )

    cred_plan = next(p for p in plans if p.ref.field == "credentials")
    assert cred_plan.to_associate == (31,)
    assert cred_plan.to_disassociate == ()
    assert cred_plan.field_change is not None
    assert cred_plan.field_change.before == ["ssh"]
    assert cred_plan.field_change.after == ["ssh", "vault"]


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


_HOST_IDS = {"web-01": 7, "web-02": 8, "web-03": 9}


@pytest.mark.parametrize(
    ("desired", "existing", "record_id", "associate", "disassociate", "change"),
    [
        # a resource that does not exist yet associates every member, without a fetch
        (["web-01", "web-02"], ["web-03"], None, [7, 8], [], ([], ["web-01", "web-02"])),
        (["web-01", "web-02"], ["web-01"], 42, [8], [], (["web-01"], ["web-01", "web-02"])),
        (["web-01"], ["web-01", "web-02"], 42, [], [8], (["web-01", "web-02"], ["web-01"])),
        (["web-01", "web-03"], ["web-01", "web-02"], 42, [9], [8],
         (["web-01", "web-02"], ["web-01", "web-03"])),
        # ``hosts: []`` is the explicit "remove every host" gesture
        ([], ["web-01", "web-02"], 42, [], [7, 8], (["web-01", "web-02"], [])),
        # identical membership keeps the diff quiet; before/after are sorted names
        (["web-01"], ["web-01"], 42, [], [], None),
        (["web-02", "web-01"], ["web-03"], 42, [7, 8], [9], (["web-03"], ["web-01", "web-02"])),
    ],
)  # fmt: skip
def test_plan_reconciles_group_hosts(
    desired: list[str],
    existing: list[str],
    record_id: int | None,
    associate: list[int],
    disassociate: list[int],
    change: tuple[list[str], list[str]] | None,
) -> None:
    client = _StubClient(
        existing_members={"hosts": [{"id": _HOST_IDS[n], "name": n} for n in existing]}
    )
    fk = _StubFk({("Host", name): id_ for name, id_ in _HOST_IDS.items()})
    [plan] = MembershipReconciler().plan(
        GROUP_SPEC,
        _group("g1", hosts=desired),
        record_id=record_id,
        client=cast(ResourceClient, client),
        fk=cast(FkResolver, fk),
    )
    assert plan.ref.field == "hosts"
    assert sorted(plan.to_associate) == associate
    assert sorted(plan.to_disassociate) == disassociate
    if change is None:
        assert plan.field_change is None
    else:
        assert plan.field_change is not None
        assert (plan.field_change.field, plan.field_change.before, plan.field_change.after) == (
            "hosts",
            *change,
        )


def test_plan_rejects_non_list_field() -> None:
    """A bare string for ``hosts:`` would be silently coerced to ``[]``
    and disassociate every member — most destructive footgun. Reject."""
    rec = MembershipReconciler()
    with pytest.raises(BadRequestError, match="must be a list of names"):
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


# ---- post_members ----


@pytest.mark.parametrize(
    ("ref", "member_ids", "disassociate", "calls"),
    [
        (GROUP_SPEC.fk_refs[0], [7, 8], False, [{"id": 7}, {"id": 8}]),
        (GROUP_SPEC.fk_refs[0], [7], True, [{"id": 7, "disassociate": True}]),
        (GROUP_SPEC.fk_refs[0], [], False, []),
        # a ref without a sub-endpoint never issues a malformed POST
        (FkRef(field="hosts", kind="Host"), [7, 8], False, []),
    ],
)
def test_post_members(
    ref: FkRef, member_ids: list[int], disassociate: bool, calls: list[dict[str, Any]]
) -> None:
    client = _StubClient()
    MembershipReconciler().post_members(
        GROUP_SPEC,
        parent_id=42,
        ref=ref,
        member_ids=member_ids,
        disassociate=disassociate,
        client=cast(ResourceClient, client),
    )
    assert client.subendpoint_calls == [(42, "hosts", "POST", body) for body in calls]


@pytest.mark.parametrize(
    "current,desired", [([1, 3], [1, 2, 3]), ([1, 3], [2, 1, 3]), ([1, 2, 3, 5], [3, 4, 1])]
)
def test_ordered_replacement_interleaves_new_and_reordered_members(
    current: list[int], desired: list[int]
) -> None:
    from awx.unit.support import _Fk, _MembershipClient
    from untaped.capabilities.awx.domain import Metadata, ResourceSpec

    spec = ResourceSpec(
        kind="Item",
        identity_keys=("name",),
        canonical_fields=(),
        fk_refs=(
            FkRef(field="members", kind="Item", multi=True, sub_endpoint="members", ordered=True),
        ),
    )
    client = _MembershipClient([])
    client.members[(10, "members")] = current.copy()
    fk = _Fk({("Item", str(item)): item for item in desired})
    use = MembershipReconciler()
    plans = use.plan(
        spec,
        Resource(
            kind="Item",
            metadata=Metadata(name="owner"),
            spec={"members": [str(item) for item in desired]},
        ),
        10,
        client=cast(ResourceClient, client),
        fk=cast(FkResolver, fk),
    )
    use.execute(spec, 10, plans, client=cast(ResourceClient, client))
    assert client.members[(10, "members")] == desired
    use.verify(spec, 10, plans, client=cast(ResourceClient, client))


@pytest.mark.parametrize("label", ["existing", "changed"])
def test_membership_validates_bound_ids_and_resolves_changed_names(label: str) -> None:
    class Resolver:
        def __init__(self) -> None:
            self.validated: list[int] = []
            self.resolved: list[str] = []

        def validate_id(self, kind: str, id_: int, *, scope: Any = None) -> int:
            self.validated.append(id_)
            return id_

        def name_to_id(self, kind: str, name: str, *, scope: Any = None) -> int:
            self.resolved.append(name)
            assert name == "changed"
            return 12

    fk = Resolver()
    client = _StubClient(existing_members={"hosts": [{"id": 9, "name": "existing"}]})
    plans = MembershipReconciler().plan(
        GROUP_SPEC,
        Resource(
            kind="Group",
            metadata=Metadata(name="group", parent=IdentityRef(kind="Inventory", name="prod")),
            spec={"hosts": [9 if label == "existing" else label]},
        ),
        1,
        client=cast(ResourceClient, client),
        fk=cast(FkResolver, fk),
    )
    assert plans[0].desired_ids == ((9,) if label == "existing" else (12,))
    assert fk.validated == ([9] if label == "existing" else [])
    assert fk.resolved == ([] if label == "existing" else ["changed"])


def test_supplied_membership_snapshot_cannot_refresh_missing_relationship() -> None:
    client = _StubClient(existing_members={"hosts": [{"id": 9, "name": "existing"}]})
    with pytest.raises(BadRequestError, match="missing initial editor membership snapshot"):
        MembershipReconciler().plan(
            GROUP_SPEC,
            _group("group", hosts=[]),
            1,
            client=cast(ResourceClient, client),
            fk=cast(FkResolver, _StubFk({})),
            membership_snapshots={},
        )
    assert not client.subendpoint_calls


def test_ordered_replacement_removes_unwanted_members_after_adding() -> None:
    ref = FkRef(field="members", kind="Item", multi=True, sub_endpoint="members", ordered=True)
    client = _StubClient()
    plan = MembershipPlan(
        ref=ref,
        to_associate=(3,),
        to_disassociate=(2,),
        field_change=None,
        existing_ids=(1, 2),
        desired_ids=(1, 3),
    )
    MembershipReconciler().execute(GROUP_SPEC, 42, [plan], client=cast(ResourceClient, client))
    assert client.subendpoint_calls == [
        (42, "members", "POST", {"id": 3}),
        (42, "members", "POST", {"id": 2, "disassociate": True}),
    ]
