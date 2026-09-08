"""Pure comparison and presentation helpers for AWX mutation plans."""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from typing import Any

from untaped.capabilities.awx.domain import ApplyOutcome, FieldChange, ResourceSpec

REDACTED = "<redacted>"


def semantic_equal(
    left: Any,
    right: Any,
    *,
    allow_server_enrichment: bool = False,
) -> bool:
    """Compare user-owned values exactly, with explicit enrichment allowance."""
    if allow_server_enrichment and isinstance(left, Mapping) and isinstance(right, Mapping):
        return bool(
            all(
                key in right and semantic_equal(value, right[key])
                for key, value in left.items()
            )
        )
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return bool(
            set(left) == set(right)
            and all(semantic_equal(left[key], right[key]) for key in left)
        )
    if isinstance(left, list) and isinstance(right, list):
        return bool(
            len(left) == len(right)
            and all(semantic_equal(a, b) for a, b in zip(left, right, strict=True))
        )
    return bool(left == right)


def redact_value(value: Any, paths: Iterable[str]) -> Any:
    """Deep-copy ``value`` and replace every known secret path."""
    result = copy.deepcopy(value)
    for path in paths:
        _redact_at_path(result, path.split("."))
    return result


def redact_field_change(change: FieldChange, spec: ResourceSpec) -> FieldChange:
    """Redact known secret values in a single presentation diff row."""
    paths = [
        ".".join(path.split(".")[1:])
        for path in spec.secret_paths
        if path.split(".", 1)[0] == change.field
    ]
    if change.field in spec.secret_paths:
        paths = [""]
    if not paths:
        return change
    before = REDACTED if paths == [""] else redact_value(change.before, paths)
    after = REDACTED if paths == [""] else redact_value(change.after, paths)
    return change.model_copy(update={"before": before, "after": after})


def redact_outcome(outcome: ApplyOutcome, spec: ResourceSpec) -> ApplyOutcome:
    return outcome.model_copy(
        update={
            "changes": [redact_field_change(change, spec) for change in outcome.changes],
        }
    )


def relative_secret_paths(paths: Iterable[str], field: str) -> list[str]:
    """Return secret patterns relative to one top-level field."""
    relative: list[str] = []
    for path in paths:
        first, separator, rest = path.partition(".")
        if first != field:
            continue
        relative.append(rest if separator else "")
    return relative


def _redact_at_path(value: Any, parts: list[str]) -> None:  # noqa: C901
    if not parts or value is None:
        return
    head, *tail = parts
    if not tail:
        if isinstance(value, dict):
            if head == "*":
                for key in list(value):
                    value[key] = REDACTED
            elif head in value:
                value[head] = REDACTED
        elif isinstance(value, list) and head == "*":
            for index in range(len(value)):
                value[index] = REDACTED
        return
    if isinstance(value, dict):
        if head == "*":
            for child in value.values():
                _redact_at_path(child, tail)
        elif head in value:
            _redact_at_path(value[head], tail)
    elif isinstance(value, list) and head == "*":
        for child in value:
            _redact_at_path(child, tail)


__all__ = [
    "REDACTED",
    "redact_field_change",
    "redact_outcome",
    "redact_value",
    "relative_secret_paths",
    "semantic_equal",
]
