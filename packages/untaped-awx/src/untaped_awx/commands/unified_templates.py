"""``untaped awx unified-templates`` — polymorphic templates browser.

Read-only surface over AWX's ``/api/v2/unified_job_templates/`` virtual
collection, which aggregates ``JobTemplate``, ``WorkflowJobTemplate``,
``Project``, and ``InventorySource`` rows behind a single ``type``
discriminator string. The AWX UI's "Templates" page shows the same view.

Browse-only by design:

- Names are not unique across kinds (a JobTemplate and a Project can
  both be called ``deploy``), so ``get`` is **id-only** — name lookup
  would silently pick the wrong row. For name lookup, drop into the
  per-kind group (``job-templates get deploy``, etc.).
- Launching stays on the per-kind groups (``job-templates launch``,
  ``projects sync``, …); polymorphic launch dispatch isn't worth the
  complexity when the per-kind path is already complete.

Its commands are written out, as ``jobs``' are, rather than derived like
a kind's group: those bake in CRUD and an identity-based ``get`` that this
virtual collection cannot satisfy.
"""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import Field

from untaped.sdk import (
    ErrorCategory,
    Failure,
    Group,
    KeyValue,
    Limit,
    Output,
    Positional,
    Stdin,
    UsageError,
    not_found,
    q,
)
from untaped_awx.application import GetUnifiedTemplate
from untaped_awx.commands._awx import AwxCommand
from untaped_awx.errors import AwxError
from untaped_awx.records import UnifiedTemplate

_DEFAULT_LIST_COLUMNS = ("id", "name", "type")
"""Strict-minimal projection: identity + the polymorphic discriminator.

Kept tight on purpose — the four kinds carry different health fields
(JT/WJT use ``last_job_status``, Project/InventorySource use ``status``)
so any health column is empty for half the rows. Users who want health
or organization context project them explicitly via ``--columns``."""


class List(AwxCommand):
    """List Unified Job Templates (alphabetical by name)."""

    type_: str | None = Field(
        None,
        description=(
            "Filter by AWX type discriminator. Common values: job_template, "
            "workflow_job_template, project, inventory_source."
        ),
    )
    filter_: list[KeyValue] = Field(
        default_factory=list,
        description="Server-side filter, KEY=VALUE (repeatable). Forwarded verbatim to AWX.",
    )
    limit: Limit
    output: Output[UnifiedTemplate] = Output(columns=_DEFAULT_LIST_COLUMNS)

    def run(self) -> Iterator[UnifiedTemplate]:
        filters = dict(self.filter_)
        if self.type_ is not None:
            if "type" in filters:
                raise UsageError(
                    "pass --type or --filter type=…, not both — they collide on the same param"
                )
            filters["type"] = self.type_
        self.output.empty("No templates found. Try a different --type or --filter.")
        with self.awx() as awx, awx.diag.progress("Loading templates"):
            records = list(awx.ujts.list(params=filters, limit=self.limit.value))
        yield from (UnifiedTemplate.model_validate(record) for record in records)


class Get(AwxCommand):
    """Fetch one or more Unified Job Templates by numeric id."""

    ids: Positional[list[str]] = Field(
        default_factory=list,
        description=(
            "Numeric Unified Job Template ids. Names are not unique across kinds, so use the "
            "per-kind group for name lookup."
        ),
    )
    stdin: Stdin[UnifiedTemplate]
    output: Output[UnifiedTemplate] = Output(columns=_DEFAULT_LIST_COLUMNS)

    def run(self) -> Iterator[UnifiedTemplate]:
        identifiers = self.stdin.identifiers(self.ids)
        for raw in identifiers:
            if not raw.isdecimal():
                # Fast-fail before hitting AWX so the error message is
                # specifically about the id-only contract instead of a
                # vague 404.
                raise UsageError(
                    f"unified-templates get takes numeric ids ({q(raw)} is not a number); "
                    "names are not unique across kinds, so use the per-kind group "
                    "for name lookup",
                )
        with self.awx() as awx:
            records, missing = GetUnifiedTemplate(awx.ujts)(ids=identifiers)
        for raw in missing:
            self.ctx.diag.failure(
                Failure(
                    category=ErrorCategory.NOT_FOUND,
                    system=AwxError.system,
                    message=not_found("unified template", raw),
                )
            )
        yield from (UnifiedTemplate.model_validate(record) for record in records)


class UnifiedTemplatesGroup(Group):
    """Browse Unified Job Templates (the polymorphic view of every launchable kind)."""

    commands = (List, Get)


__all__ = ["Get", "List", "UnifiedTemplatesGroup"]
