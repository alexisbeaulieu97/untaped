"""Use case: associate or disassociate sub-endpoint members on a parent.

Drives the same write path the apply pipeline uses
(:meth:`MembershipReconciler.post_members`) from an already-resolved list
of member ids, so a quick `groups hosts add ...` CLI invocation doesn't
have to round-trip through a YAML envelope and a desired-state diff.
"""

from __future__ import annotations

from typing import Literal

from untaped.capabilities.awx.application.apply_membership import (
    MembershipPlan,
    MembershipReconciler,
)
from untaped.capabilities.awx.application.ports import ResourceClient
from untaped.capabilities.awx.domain import FkRef, ResourceSpec


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
        plan = self.prepare(
            spec, parent_id=parent_id, ref=ref, member_ids=member_ids, action=action
        )
        self.execute(spec, parent_id=parent_id, plan=plan)

    def prepare(
        self,
        spec: ResourceSpec,
        *,
        parent_id: int,
        ref: FkRef,
        member_ids: list[int],
        action: Literal["associate", "disassociate"],
    ) -> MembershipPlan:
        """Capture additive deltas before preview without writing."""
        return self._reconciler.plan_additive(
            spec,
            parent_id,
            ref,
            member_ids,
            disassociate=action == "disassociate",
            client=self._client,
        )

    def execute(self, spec: ResourceSpec, *, parent_id: int, plan: MembershipPlan) -> None:
        """Execute the prepared deltas and verify requested presence/absence."""
        self._reconciler.execute(spec, parent_id, [plan], client=self._client)
        self._reconciler.verify(spec, parent_id, [plan], client=self._client)
