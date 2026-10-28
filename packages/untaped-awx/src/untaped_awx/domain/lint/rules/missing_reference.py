"""``awx.missing-reference``: a reference names nothing in its origin."""

from __future__ import annotations

from collections.abc import Iterator

from untaped.sdk import experimental
from untaped_awx.domain.lint.resources import Linted, Reference, Resources
from untaped_awx.domain.lint.rules._support import report
from untaped_lint.api import Finding, rule


@experimental
@rule(
    "missing-reference",
    reads=Resources,
    severity="error",
    summary="Every reference names a resource that exists: in the batch or on the controller.",
)
def missing_reference(resources: Resources) -> Iterator[Finding]:
    for item in resources.items:
        for ref in item.references:
            if ref.resolution == "absent":
                message = _message(item, ref, online=resources.online)
                yield report(missing_reference, item, message, hint=_hint(item, ref))


def _message(item: Linted, ref: Reference, *, online: bool) -> str:
    target = ref.target
    if not target.name:
        if ref.field.startswith("nodes["):
            return f"{ref.field} runs nothing untaped can name: its template is gone"
        return f"{ref.field} is empty"
    if item.origin == "controller":
        where = "the controller"
    else:
        where = "the batch or on the controller" if online else "the batch"
    return f"{ref.field} {target.name!r} is not in {where}"


def _hint(item: Linted, ref: Reference) -> str:
    target = ref.target
    if not target.name:
        if ref.field.startswith("nodes["):
            return "point the node at a template, or remove it"
        return f"set {ref.field} to an existing {target.kind}"
    scope = f" in {target.organization}" if target.organization else ""
    if target.parent is not None:
        scope = f" in {target.parent.kind} {target.parent.name!r}"
    if item.origin == "controller":
        return f"create {target.kind} {target.name!r}{scope}, or change {ref.field}"
    return f"add a {target.kind} document named {target.name!r}{scope}, or fix the name"
