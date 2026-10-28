"""``awx.unknown-field``: a document field untaped has no metadata for.

``apply`` sends such a field as it is and warns; the rule reports it with an
id, so a typo fails a gate before it reaches the controller. Live resources
are read through the catalog's own fields, so it fires on documents only.
"""

from __future__ import annotations

from collections.abc import Iterator

from untaped.sdk import experimental, plural
from untaped_awx.domain.lint.resources import Resources
from untaped_awx.domain.lint.rules._support import report
from untaped_awx.domain.spec import unrecognized_names
from untaped_lint.api import Finding, rule


@experimental
@rule(
    "unknown-field",
    reads=Resources,
    severity="warning",
    summary="A document's spec holds only fields untaped knows for its kind.",
)
def unknown_field(resources: Resources) -> Iterator[Finding]:
    for item in resources.items:
        facts = resources.kinds.get(item.resource.kind)
        if item.origin != "document" or facts is None:
            continue
        unknown = unrecognized_names(item.resource.spec, facts.known_fields)
        if unknown:
            yield report(
                unknown_field,
                item,
                f"{plural(len(unknown), 'field')} untaped does not know for "
                f"{item.resource.kind}: {', '.join(unknown)}",
                hint="fix the name; apply sends an unknown field as it is",
            )
