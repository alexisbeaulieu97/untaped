"""Result records produced by the apply / save use cases."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ApplyAction = Literal[
    "preview",
    "created",
    "updated",
    "unchanged",
    "skipped",
    "failed",
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
    """The result of applying a single :class:`Resource`.

    Frozen so that the parallel branch in
    :class:`untaped_awx.application.apply_file.ApplyFile._apply_kind`
    can't silently regress into in-place mutations of an outcome shared
    across workers — phase 2's rewrites go through
    :meth:`pydantic.BaseModel.model_copy` with ``update={...}``. See
    ``packages/untaped-awx/AGENTS.md`` "Apply parallelism".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    name: str
    action: ApplyAction
    changes: list[FieldChange] = Field(default_factory=list)
    preserved_secrets: list[str] = Field(default_factory=list)
    dropped_undeclared_secrets: list[str] = Field(default_factory=list)
    detail: str | None = None
