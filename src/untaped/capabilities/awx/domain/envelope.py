"""kubectl-style envelope for saved AWX resources.

A saved YAML doc has the shape::

    kind: JobTemplate
    apiVersion: untaped.dev/awx/v1
    metadata:
      name: deploy-app
      organization: Default
    spec:
      ...

``metadata`` carries identity (the "what is this object?" answer);
``spec`` carries declared state (the "what should it look like?" answer).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

API_VERSION = "untaped.dev/awx/v1"


class IdentityRef(BaseModel):
    """A reference to another resource by its identity (used for polymorphic FKs).

    Schedule's ``metadata.parent`` is the canonical example: a schedule
    can attach to a JobTemplate, WorkflowJobTemplate, Project, or
    InventorySource — discriminated by ``kind``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    name: str
    organization: str | None = None
    parent: IdentityRef | None = None

    def lookup_scope(self, required: dict[str, str] | None = None) -> dict[str, str]:
        """Translate ancestry and refine required scope without allowing an override."""
        scope = dict(required or {})
        child = self.kind in {"Host", "Group", "InventorySource"}
        explicit: dict[str, str] = {}
        if self.organization is not None:
            explicit["inventory__organization" if child else "organization"] = self.organization
        if self.parent is not None:
            if not child or self.parent.kind != "Inventory" or self.parent.parent is not None:
                raise ValueError(f"unsupported parent ancestry for {self.kind}")
            explicit["inventory"] = self.parent.name
            if self.parent.organization is not None:
                if (
                    explicit.get("inventory__organization", self.parent.organization)
                    != self.parent.organization
                ):
                    raise ValueError("foreign key organization conflicts with parent ancestry")
                explicit["inventory__organization"] = self.parent.organization
        for key, value in explicit.items():
            if key in scope and scope[key] != value:
                raise ValueError(f"foreign key ancestry conflicts with required scope {key!r}")
            scope[key] = value
        if self.kind == "InventorySource" and "inventory" not in scope:
            raise ValueError("InventorySource reference requires Inventory parent ancestry")
        return scope


class Metadata(BaseModel):
    """Identity slice of a Resource doc.

    Most kinds use ``name`` + ``organization`` as the uniqueness key.
    Schedules also carry a polymorphic ``parent``.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    organization: str | None = None
    parent: IdentityRef | None = None


class Resource(BaseModel):
    """A single saved AWX resource (kubectl-style envelope)."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    apiVersion: str = API_VERSION
    metadata: Metadata
    spec: dict[str, Any] = Field(default_factory=dict)
