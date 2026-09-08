"""Value objects shared by AWX batch planning and execution."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from untaped.capabilities.awx.application.apply_membership import MembershipPlan
from untaped.capabilities.awx.application.mutation_refs import DeferredReference
from untaped.capabilities.awx.application.ports import ApplyStrategy
from untaped.capabilities.awx.domain import ApplyOutcome, Resource, ResourceSpec


@dataclass
class PreparedMutation:
    """One fixed target and its preflight state.

    ``payload`` and ``existing`` are execution-only values and are excluded
    from repr so printing a plan cannot accidentally expose a secret.  Use
    ``presentation_payload`` and ``preview`` for human-facing output.
    """

    index: int
    resource: Resource = field(repr=False)
    spec: ResourceSpec = field(repr=False)
    strategy: ApplyStrategy = field(repr=False)
    identity: dict[str, Any]
    scope: dict[str, Any]
    payload: dict[str, Any] = field(repr=False)
    presentation_payload: dict[str, Any]
    existing: dict[str, Any] | None = field(repr=False)
    target_id: int | None
    create: bool
    dependencies: tuple[int, ...]
    preview: ApplyOutcome
    membership_plans: list[MembershipPlan] = field(repr=False, default_factory=list)
    resolver: Any = field(repr=False, default=None)


@dataclass
class MutationPlan:
    """An in-memory plan that can be confirmed and executed unchanged."""

    operations: list[PreparedMutation]
    mode: str = "apply"

    @property
    def empty(self) -> bool:
        return not self.operations


def copy_identity(identity: dict[str, Any]) -> dict[str, Any]:
    """Copy an identity without retaining mutable Pydantic internals."""
    return copy.deepcopy(identity)


__all__ = ["DeferredReference", "MutationPlan", "PreparedMutation", "copy_identity"]
