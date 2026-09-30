"""Result records produced by the apply / save use cases."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from untaped.capabilities.awx.domain.envelope import Resource
from untaped.capability_api import OutcomeRecord, UtcTimestamp

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


class CopyOutcome(OutcomeRecord):
    """One ``copy`` row: the new resource (``id``, ``name``) and its source.

    ``kind`` is the resource kind (``JobTemplate``), so ``patch --stdin`` on
    that kind selects the copy by ``id``. ``action`` is ``planned`` or
    ``created``; ``not_carried`` lists what AWX reported it would not copy.
    """

    id: int | None = None
    name: str
    source_id: int
    kind: str
    action: str
    not_carried: list[str] = Field(default_factory=list)
    detail: str | None = None


class RenameOutcome(OutcomeRecord):
    """One ``rename`` row: the resource (``id``), its new and previous names.

    ``kind`` is the resource kind (``JobTemplate``), so ``patch --stdin`` on
    that kind selects the renamed resource by ``id``. ``action`` is
    ``planned``, ``renamed`` (the re-read shows the new name) or ``failed``.
    """

    id: int
    name: str
    old_name: str
    kind: str
    action: str
    detail: str | None = None


class TemporaryCopyOutcome(OutcomeRecord):
    """One copy of an ``awx test`` temporary set: ``planned`` (``validate --source-ref``),
    ``deleted``, ``failed`` or ``kept`` (a run's teardown, ``test prune``).

    ``kind`` is the resource kind (``JobTemplate``); ``template`` is the name
    it was copied from, and ``run_id``, ``ref``, ``sha`` and ``created_at``
    come from its description marker. A planned copy names the spec it is
    read from (``path``) and the launch prompts it enables for its cases
    (``prompts``).
    """

    id: int | None = None
    name: str
    kind: str
    template: str
    organization: str | None = None
    run_id: str
    ref: str
    sha: str
    created_at: UtcTimestamp
    path: str | None = None
    prompts: list[str] = Field(default_factory=list)
    action: str
    detail: str | None = None


class DeleteReceipt(BaseModel):
    """A successful DELETE acknowledges removal or asynchronous acceptance."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    action: Literal["deleted", "deletion_requested"]
