"""``awx.invalid-document``: a document the reader could not read as an AWX resource."""

from __future__ import annotations

from collections.abc import Iterator

from untaped.sdk import experimental
from untaped_awx.domain.lint.resources import AwxResources
from untaped_lint.api import DocumentLocation, Finding, finding, rule


@experimental
@rule(
    "invalid-document",
    reads=AwxResources,
    severity="error",
    summary="Every document is a valid resource of a kind untaped knows.",
)
def invalid_document(resources: AwxResources) -> Iterator[Finding]:
    for each in resources.unreadable:
        yield finding(
            invalid_document,
            each.path,
            each.error,
            location=DocumentLocation(path=each.path, index=each.index),
        )
