"""Result records produced by the apply / save use cases."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from untaped.capabilities.awx.domain.envelope import Resource
from untaped.capability_api import OutcomeRecord

ApplyAction = Literal[
    "planned",
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


class ApplyOutcome(OutcomeRecord):
    """The result of applying a single :class:`Resource`.

    Frozen (via :class:`OutcomeRecord`) so phase 2's rewrites must produce a
    new instance (``model_copy(update=...)``) instead of mutating one shared
    across workers.
    """

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


class JobCancelOutcome(OutcomeRecord):
    """One ``jobs cancel`` row: the execution, its status when read, and the result.

    ``action`` is ``planned``, ``skipped`` (already finished),
    ``cancel_requested`` (AWX accepted the request; the job stops
    asynchronously) or ``failed``.
    """

    id: int
    kind: str
    name: str | None = None
    status: str | None = None
    action: str
    detail: str | None = None


class JobRelaunchOutcome(OutcomeRecord):
    """One ``jobs relaunch`` row: the new execution (``id``) and the job it repeats.

    ``action`` is ``planned``, ``relaunched`` or ``failed``; ``hosts`` is
    ``all`` or ``failed``.
    """

    id: int | None = None
    kind: str
    name: str | None = None
    status: str | None = None
    target_id: int
    hosts: str = "all"
    action: str
    detail: str | None = None


class DeleteReceipt(BaseModel):
    """A successful DELETE acknowledges removal or asynchronous acceptance."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    action: Literal["deleted", "deletion_requested"]
