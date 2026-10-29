"""A template's one-resource writes: ``copy SOURCE --name NEW`` and ``rename SOURCE NEW``."""

from __future__ import annotations

from pydantic import Field

from untaped.sdk import Confirm, Output, Positional, q
from untaped_awx.application.resources.copy_resource import CopyResource
from untaped_awx.application.resources.rename_resource import RenameResource
from untaped_awx.commands._awx import ResourceCommand
from untaped_awx.commands._format import format_scope
from untaped_awx.commands._options import ById, OrganizationScope, scope_values
from untaped_awx.commands._selection import select_resources
from untaped_awx.domain import CopyOutcome, RenameOutcome
from untaped_awx.errors import BadRequestError


class Copy(ResourceCommand):
    """Copy one resource under a new name in the same scope, after a preview.

    AWX copies it server-side in the source's organization. A taken name,
    the source's own name, or a source AWX cannot copy (`can_copy: false`)
    is refused before writing. A warning names the parts AWX will not carry
    without user input, such as a workflow's references the caller cannot
    use; the `awx.copy_outcome` row lists them in `not_carried`, and its
    `id` is the new template's. To copy to another controller, export,
    change `metadata.name` and apply (the `Document` guide of `awx apply`).

    Examples
    --------
    ```console
    $ untaped awx job-templates copy Deploy --name "Deploy next" --organization Default --dry-run
    ```
    """

    source: Positional[str] = Field(
        description="Name of the resource to copy, or its AWX id with --by-id."
    )
    name: str = Field(description="Name of the new copy.")
    scope: OrganizationScope
    by_id: ById = Field(description="Look up the source by its AWX id instead of its name.")
    confirm: Confirm
    output: Output[CopyOutcome]

    def run(self) -> CopyOutcome:
        spec = self.spec
        with self.awx() as awx:
            (selected,) = select_resources(
                awx, spec, [self.source], by_id=self.by_id, scope=scope_values(self.scope)
            )
            copier = CopyResource(awx.repo)
            plan = copier.plan(spec, selected, self.name)
            preview = (
                f"Copy {spec.kind}/{selected.name} id={selected.id} "
                f"scope={format_scope(selected.scope)} -> {q(self.name)}"
            )
            for part in plan.not_carried:
                awx.diag.warning(f"AWX will not copy {part}")
            outcome = CopyOutcome(
                name=self.name,
                source_id=selected.id,
                kind=spec.kind,
                action="planned",
                not_carried=list(plan.not_carried),
            )
            if self.confirm("Copy 1 resource?", preview=lambda: [preview]):
                outcome = copier(spec, plan)
        return outcome


class Rename(ResourceCommand):
    """Rename one resource in place, after a preview, and verify the new name.

    The current name and a name already used in the same scope (the
    organization, or none for an org-less workflow) are refused. The
    template is re-read after the write: a name AWX did not take fails the
    row (exit 1). The `awx.rename_outcome` row carries `old_name`.

    Examples
    --------
    ```console
    $ untaped awx job-templates rename Deploy "Deploy app" --organization Default --dry-run
    ```
    """

    source: Positional[str] = Field(
        description="Current name of the resource, or its AWX id with --by-id."
    )
    new: Positional[str] = Field(description="New name for the resource.")
    scope: OrganizationScope
    by_id: ById = Field(description="Look up the source by its AWX id instead of its name.")
    confirm: Confirm
    output: Output[RenameOutcome]

    def run(self) -> RenameOutcome:
        spec = self.spec
        with self.awx() as awx:
            (selected,) = select_resources(
                awx, spec, [self.source], by_id=self.by_id, scope=scope_values(self.scope)
            )
            renamer = RenameResource(awx.repo)
            plan = renamer.plan(spec, selected, self.new)
            preview = (
                f"Rename {spec.kind} id={selected.id} scope={format_scope(selected.scope)}: "
                f"{q(selected.name or '')} -> {q(self.new)}"
            )
            outcome = plan.outcome(spec, action="planned")
            if self.confirm("Rename 1 resource?", preview=lambda: [preview]):
                outcome = renamer(spec, plan)
        if outcome.failed:
            awx.diag.failure(BadRequestError(f"{spec.kind}#{outcome.id}: {outcome.detail}"))
        return outcome


__all__ = ["Copy", "Rename"]
