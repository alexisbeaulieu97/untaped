"""``awx.unresolved-reference``: a reference that was not checked this run."""

from __future__ import annotations

from collections.abc import Iterator

from untaped.sdk import experimental
from untaped_awx.domain.lint.resources import AwxResources
from untaped_awx.domain.lint.rules._support import report
from untaped_lint.api import Finding, rule


@experimental
@rule(
    "unresolved-reference",
    reads=AwxResources,
    severity="warning",
    summary="A reference the run could not check: offline with no document of its kind.",
)
def unresolved_reference(resources: AwxResources) -> Iterator[Finding]:
    for item in resources.items:
        for ref in item.references:
            if ref.resolution != "unresolved":
                continue
            target = ref.target
            if not resources.online:
                why = f"no {target.kind} documents, offline"
                hint = "lint without --offline to check it on the controller, or add its document"
            else:
                why = f"the token cannot read {target.kind} records"
                hint = f"lint with a token that can read {target.kind} records"
            yield report(
                unresolved_reference,
                item,
                f"{ref.field} {target.name!r} was not checked: {why}",
                hint=hint,
            )
