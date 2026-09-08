"""Unit tests for ManageMembership.

Drives the same write path as the apply pipeline (delegates to
``MembershipReconciler.post_members``) from an already-resolved id list,
so the test focuses on the bool-flag translation and the POST-call
shapes the stub client receives.
"""

from __future__ import annotations

from typing import Any, cast

from untaped.capabilities.awx.application.manage_membership import ManageMembership
from untaped.capabilities.awx.application.ports import ResourceClient
from untaped.capabilities.awx.domain import ResourceSpec
from untaped.capabilities.awx.infrastructure.specs import GROUP_SPEC


class _StubClient:
    def __init__(self) -> None:
        self.members: set[int] = set()
        self.calls: list[tuple[int, str, str, dict[str, Any] | None]] = []

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
        self.calls.append((record_id, sub_endpoint, method, json))
        if json is not None:
            if json.get("disassociate"):
                self.members.discard(json["id"])
            else:
                self.members.add(json["id"])
        return {}

    def paginate_sub_endpoint(self, spec: ResourceSpec, record_id: int, sub_endpoint: str) -> Any:
        return iter({"id": item} for item in self.members)


def _hosts_ref() -> Any:
    return next(r for r in GROUP_SPEC.fk_refs if r.field == "hosts")


def _children_ref() -> Any:
    return next(r for r in GROUP_SPEC.fk_refs if r.field == "children")


def test_associate_posts_one_request_per_member() -> None:
    client = _StubClient()
    ManageMembership(cast(ResourceClient, client))(
        GROUP_SPEC,
        parent_id=200,
        ref=_hosts_ref(),
        member_ids=[101, 102],
        action="associate",
    )
    assert client.calls == [
        (200, "hosts", "POST", {"id": 101}),
        (200, "hosts", "POST", {"id": 102}),
    ]


def test_disassociate_sets_disassociate_flag() -> None:
    client = _StubClient()
    ManageMembership(cast(ResourceClient, client))(
        GROUP_SPEC,
        parent_id=200,
        ref=_hosts_ref(),
        member_ids=[101],
        action="disassociate",
    )
    assert client.calls == [
        (200, "hosts", "POST", {"id": 101, "disassociate": True}),
    ]


def test_empty_member_list_is_a_noop() -> None:
    """No members → no POSTs. Lets the CLI fold per-id resolution errors
    without forcing an empty round trip to AWX."""
    client = _StubClient()
    ManageMembership(cast(ResourceClient, client))(
        GROUP_SPEC,
        parent_id=200,
        ref=_hosts_ref(),
        member_ids=[],
        action="associate",
    )
    assert client.calls == []


def test_children_sub_endpoint_uses_ref_path() -> None:
    """Spec-driven: any ``FkRef(multi=True, sub_endpoint=X)`` routes to
    ``<api_path>/<id>/X/``, so the same use case handles ``children``
    without per-kind plumbing."""
    client = _StubClient()
    ManageMembership(cast(ResourceClient, client))(
        GROUP_SPEC,
        parent_id=200,
        ref=_children_ref(),
        member_ids=[300, 301],
        action="associate",
    )
    assert client.calls == [
        (200, "children", "POST", {"id": 300}),
        (200, "children", "POST", {"id": 301}),
    ]


def test_additive_verification_retains_unrelated_members() -> None:
    from tests.awx.unit.test_mutation_engine import _MembershipClient

    client = _MembershipClient([])
    client.members[(200, "hosts")] = [999]
    use = ManageMembership(cast(ResourceClient, client))
    use(GROUP_SPEC, parent_id=200, ref=_hosts_ref(), member_ids=[101], action="associate")
    assert client.members[(200, "hosts")] == [999, 101]
    use(GROUP_SPEC, parent_id=200, ref=_hosts_ref(), member_ids=[101], action="disassociate")
    assert client.members[(200, "hosts")] == [999]


def test_additive_ignored_write_fails_readback() -> None:
    import pytest

    from tests.awx.unit.test_mutation_engine import _MembershipClient
    from untaped.capabilities.awx.errors import BadRequest

    client = _MembershipClient([])
    client.ignore_membership = True
    with pytest.raises(BadRequest, match="did not converge"):
        ManageMembership(cast(ResourceClient, client))(
            GROUP_SPEC, parent_id=200, ref=_hosts_ref(), member_ids=[101], action="associate"
        )
