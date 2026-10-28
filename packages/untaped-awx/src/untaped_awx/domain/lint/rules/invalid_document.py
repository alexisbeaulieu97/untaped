"""``awx.invalid-document``: a document the reader could not read as an AWX resource.

The reader's error can run to several lines (a YAML parser's context, one
line per validation error); the message is its first sentence and the rest
goes in the hint, on one line.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from untaped.sdk import experimental
from untaped_awx.domain.lint.resources import Resources
from untaped_lint.api import DocumentLocation, Finding, finding, rule

_VALIDATION = re.compile(r"^\d+ validation errors? for \w+$")
_DETAIL = re.compile(r"\s*\[type=.*\]$")
_AVAILABLE = re.compile(r"^(.*) \((available: .*)\)$")
_NOISE = ("For further information", "^")


@experimental
@rule(
    "invalid-document",
    reads=Resources,
    severity="error",
    summary="Every document is a valid resource of a kind untaped knows.",
)
def invalid_document(resources: Resources) -> Iterator[Finding]:
    for each in resources.unreadable:
        message, hint = _sentence(each.error)
        yield finding(
            invalid_document,
            each.path,
            message,
            location=DocumentLocation(path=each.path, index=each.index),
            hint=hint,
        )


def _sentence(error: str) -> tuple[str, str | None]:
    """``error`` as one sentence and, when it says more, the rest on one line."""
    lines = [
        line.rstrip()
        for line in error.splitlines()
        if line.strip() and not line.strip().startswith(_NOISE)
    ]
    if not lines:
        return error, None
    if _VALIDATION.match(lines[0]):
        problems: list[str] = []
        where = ""
        for line in lines[1:]:
            if line.startswith(" "):
                problems.append(f"{where}: {_DETAIL.sub('', line.strip())}")
            else:
                where = line.strip()
        if problems:
            return f"not a valid resource: {problems[0]}", "; ".join(problems[1:]) or None
    if len(lines) == 1 and (listed := _AVAILABLE.match(lines[0])):
        return listed[1], listed[2]
    rest = " ".join(line.strip() for line in lines[1:])
    rest = rest.replace('in "<unicode string>", ', "at ").rstrip(":")
    return lines[0], rest or None
