"""Rename one resource whose identity is its name, then prove the write.

``patch`` refuses identity fields, so a rename is its own explicit operation:
planning refuses a new name already used in the resource's scope, the write
sends only ``name``, and a re-read must show the new name before the rename
counts as done.
"""

from __future__ import annotations

from dataclasses import dataclass

from untaped.capabilities.awx.application.ports import ResourceClient
from untaped.capabilities.awx.application.resource_names import name_taken
from untaped.capabilities.awx.application.selection import SelectedResource
from untaped.capabilities.awx.domain import RenameOutcome, ResourceSpec, WritePayload
from untaped.capabilities.awx.errors import BadRequestError, ConflictError


@dataclass(frozen=True)
class RenamePlan:
    """A validated rename of ``target`` to ``name``."""

    target: SelectedResource
    name: str

    def outcome(
        self, spec: ResourceSpec, *, action: str, detail: str | None = None
    ) -> RenameOutcome:
        """This plan as an ``awx.rename_outcome`` row."""
        return RenameOutcome(
            id=self.target.id,
            name=self.name,
            old_name=self.target.name or "",
            kind=spec.kind,
            action=action,
            detail=detail,
        )


class RenameResource:
    """Plan and execute a single verified rename."""

    def __init__(self, client: ResourceClient) -> None:
        self._client = client

    def plan(self, spec: ResourceSpec, target: SelectedResource, name: str) -> RenamePlan:
        """Refuse before any write when the new name is empty, unchanged or taken."""
        if not name.strip():
            raise BadRequestError("the new name must not be empty")
        if name == target.name:
            raise ConflictError(f"{spec.kind} {name!r} already has that name")
        if name_taken(self._client, spec, target.record, name):
            raise ConflictError(f"{spec.kind} {name!r} already exists in the same scope")
        return RenamePlan(target=target, name=name)

    def __call__(self, spec: ResourceSpec, plan: RenamePlan) -> RenameOutcome:
        """PATCH the name, re-read the resource, and report whether it converged."""
        self._client.update(spec, plan.target.id, WritePayload(name=plan.name))
        observed = self._client.get(spec, plan.target.id).model_dump().get("name")
        if observed != plan.name:
            return plan.outcome(
                spec,
                action="failed",
                detail=f"re-read shows name {observed!r}, not {plan.name!r}",
            )
        return plan.outcome(spec, action="renamed")


__all__ = ["RenamePlan", "RenameResource"]
