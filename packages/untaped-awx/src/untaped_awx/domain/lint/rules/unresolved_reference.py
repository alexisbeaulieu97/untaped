"""``awx.unresolved-reference``: a reference that was not checked this run.

Why it was not is read from the snapshot: offline, a kind with no document
in the batch, or an id (only the controller knows ids); online, a kind the
token cannot list, or a record it cannot read.
"""

from __future__ import annotations

from collections.abc import Iterator

from untaped.sdk import experimental
from untaped_awx.domain.lint.resources import Reference, Resources
from untaped_awx.domain.lint.rules._support import report
from untaped_lint.api import Finding, rule


@experimental
@rule(severity="warning")
def unresolved_reference(resources: Resources) -> Iterator[Finding]:
    """A reference the run could not check: offline, or one the token cannot read."""
    for item in resources.items:
        for ref in item.references:
            if ref.resolution != "unresolved":
                continue
            why, hint = _why(resources, ref)
            yield report(
                unresolved_reference,
                item,
                f"{ref.field} {ref.target.name!r} was not checked: {why}",
                hint=hint,
            )


def _why(resources: Resources, ref: Reference) -> tuple[str, str]:
    """Why ``ref`` went unchecked, and what would check it."""
    kind = ref.target.kind
    searched = kind in resources.loaded
    if not resources.online:
        check = "lint without --offline to check it on the controller"
        if searched:
            return "an id is checked on the controller only, offline", check
        facts = resources.kinds.get(kind)
        if facts is None or not facts.read_only:
            check += ", or add its document"
        return f"no {kind} documents, offline", check
    if kind == "UnifiedJobTemplate":  # a schedule's parent the token could not read
        return "the token cannot read its template", "lint with a token that can read the template"
    hint = f"lint with a token that can read {kind} records"
    if searched:
        return f"the token cannot read that {kind}", hint
    return f"the token cannot list {kind} records", hint
