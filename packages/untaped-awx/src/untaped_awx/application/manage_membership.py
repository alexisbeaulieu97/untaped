"""Use case: associate or disassociate sub-endpoint members on a parent.

Drives the same write path the apply pipeline uses
(:class:`MembershipReconciler.execute`) from an already-resolved list of
member ids, so a quick `groups hosts add ...` CLI invocation doesn't
have to round-trip through a YAML envelope and a desired-state diff.
"""

from __future__ import annotations

from typing import Literal

from untaped_awx.application.apply_membership import MembershipPlan, MembershipReconciler
from untaped_awx.application.ports import ResourceClient
from untaped_awx.domain import FkRef, ResourceSpec


class ManageMembership:
    """Associate or disassociate members on a parent's sub-endpoint."""

    def __init__(self, client: ResourceClient) -> None:
        self._client = client
        self._reconciler = MembershipReconciler()

    def __call__(
        self,
        spec: ResourceSpec,
        *,
        parent_id: int,
        ref: FkRef,
        member_ids: list[int],
        action: Literal["associate", "disassociate"],
    ) -> None:
        if not member_ids:
            return
        plan = MembershipPlan(
            ref=ref,
            to_associate=tuple(member_ids) if action == "associate" else (),
            to_disassociate=tuple(member_ids) if action == "disassociate" else (),
            field_change=None,
        )
        self._reconciler.execute(spec, parent_id, [plan], client=self._client)
