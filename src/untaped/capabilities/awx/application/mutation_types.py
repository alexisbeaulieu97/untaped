"""Value objects shared by AWX batch planning and execution."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from untaped.capabilities.awx.application.apply_membership import MembershipPlan
from untaped.capabilities.awx.application.mutation_refs import DeferredReference
from untaped.capabilities.awx.application.ports import ApplyStrategy
from untaped.capabilities.awx.domain import ApplyOutcome, Resource, ResourceSpec


@dataclass(frozen=True)
class PreparedMutation:
    """One fixed target and its preflight state.

    ``payload`` and ``existing`` are execution-only values and are excluded
    from repr so printing a plan cannot accidentally expose a secret.  Use
    ``presentation_payload`` and ``preview`` for human-facing output.
    Structured accessors return copies so callers cannot alter execution state
    through a nested mapping or an exposed Pydantic value.
    """

    index: int
    _resource: Resource = field(repr=False)
    spec: ResourceSpec = field(repr=False)
    strategy: ApplyStrategy = field(repr=False)
    _identity: dict[str, Any]
    _scope: dict[str, Any]
    _payload: dict[str, Any] = field(repr=False)
    _presentation_payload: dict[str, Any]
    _existing: dict[str, Any] | None = field(repr=False)
    target_id: int | None
    watched_fields: tuple[str, ...]
    create: bool
    dependencies: tuple[int, ...]
    _preview: ApplyOutcome
    _membership_plans: list[MembershipPlan] = field(repr=False, default_factory=list)
    create_parent: tuple[str, int | DeferredReference] | None = field(repr=False, default=None)

    @property
    def resource(self) -> Resource:
        return copy.deepcopy(self._resource)

    @property
    def identity(self) -> dict[str, Any]:
        return copy.deepcopy(self._identity)

    @property
    def scope(self) -> dict[str, Any]:
        return copy.deepcopy(self._scope)

    @property
    def payload(self) -> dict[str, Any]:
        return copy.deepcopy(self._payload)

    @property
    def presentation_payload(self) -> dict[str, Any]:
        return copy.deepcopy(self._presentation_payload)

    @property
    def existing(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._existing)

    @property
    def preview(self) -> ApplyOutcome:
        return copy.deepcopy(self._preview)

    @property
    def membership_plans(self) -> list[MembershipPlan]:
        return copy.deepcopy(self._membership_plans)


@dataclass(frozen=True)
class MutationPlan:
    """An in-memory plan that can be confirmed and executed unchanged."""

    operations: tuple[PreparedMutation, ...]
    mode: str = "apply"

    @property
    def empty(self) -> bool:
        return not self.operations


def copy_identity(identity: dict[str, Any]) -> dict[str, Any]:
    """Copy an identity without retaining mutable Pydantic internals."""
    return copy.deepcopy(identity)


__all__ = ["DeferredReference", "MutationPlan", "PreparedMutation", "copy_identity"]
