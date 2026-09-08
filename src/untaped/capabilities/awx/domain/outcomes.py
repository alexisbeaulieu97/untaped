"""Result records produced by the apply / save use cases."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from untaped.capabilities.awx.domain.envelope import Resource

ApplyAction = Literal[
    "preview",
    "created",
    "updated",
    "unchanged",
    "skipped",
    "failed",
    "conflict",
    "partial",
]


class FieldChange(BaseModel):
    """One row of an apply diff."""

    model_config = ConfigDict(frozen=True)

    field: str
    before: Any = None
    after: Any = None
    note: str | None = None
    """Optional annotation, e.g. ``preserved existing secret``."""


class ApplyOutcome(BaseModel):
    """The result of applying a single :class:`Resource`."""

    # Frozen so phase 2's rewrites must produce a new instance (via
    # `model_copy(update=...)`) instead of mutating one shared across
    # workers. See `AGENTS.md` "Apply parallelism".
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    name: str
    action: ApplyAction
    id: int | None = None
    identity: dict[str, Any] = Field(default_factory=dict)
    scope: dict[str, Any] = Field(default_factory=dict)
    changes: list[FieldChange] = Field(default_factory=list)
    preserved_secrets: list[str] = Field(default_factory=list)
    dropped_undeclared_secrets: list[str] = Field(default_factory=list)
    partial: bool = False
    unverified: bool = False
    detail: str | None = None


class BatchResult(BaseModel):
    """Outcome of one prepared mutation batch.

    The list preserves plan order, including operations that were skipped
    after a conflict or runtime failure.  ``partial`` is deliberately an
    explicit field: callers must not infer atomicity from a list containing a
    mixture of successful and failed rows.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcomes: list[ApplyOutcome] = Field(default_factory=list)
    partial: bool = False
    detail: str | None = None


SaveAction = Literal["saved", "skipped"]


class SaveOutcome(BaseModel):
    """The result of saving or skipping one resource record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    name: str | None = None
    action: SaveAction
    resource: Resource | None = None
    filename: str | None = None
    header_comment: str | None = None
    detail: str | None = None
