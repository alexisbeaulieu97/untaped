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
