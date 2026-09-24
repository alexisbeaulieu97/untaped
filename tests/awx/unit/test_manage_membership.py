"""ManageMembership (``<kind> hosts add/remove``) verifies additive writes by readback."""

from __future__ import annotations

from typing import Any, cast

import pytest

from awx.unit.support import _MembershipClient
from untaped.capabilities.awx.application.manage_membership import ManageMembership
from untaped.capabilities.awx.application.ports import ResourceClient
from untaped.capabilities.awx.errors import BadRequestError
from untaped.capabilities.awx.infrastructure.specs import GROUP_SPEC


def _hosts_ref() -> Any:
    return next(r for r in GROUP_SPEC.fk_refs if r.field == "hosts")


def test_additive_verification_retains_unrelated_members() -> None:
    client = _MembershipClient([])
    client.members[(200, "hosts")] = [999]
    use = ManageMembership(cast(ResourceClient, client))
    use(GROUP_SPEC, parent_id=200, ref=_hosts_ref(), member_ids=[101], action="associate")
    assert client.members[(200, "hosts")] == [999, 101]
    use(GROUP_SPEC, parent_id=200, ref=_hosts_ref(), member_ids=[101], action="disassociate")
    assert client.members[(200, "hosts")] == [999]


def test_additive_ignored_write_fails_readback() -> None:
    client = _MembershipClient([])
    client.ignore_membership = True
    with pytest.raises(BadRequestError, match="did not converge"):
        ManageMembership(cast(ResourceClient, client))(
            GROUP_SPEC, parent_id=200, ref=_hosts_ref(), member_ids=[101], action="associate"
        )
