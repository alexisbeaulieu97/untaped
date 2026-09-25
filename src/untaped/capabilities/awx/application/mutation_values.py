"""Pure comparison and presentation helpers for AWX mutation plans."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable, Mapping
from typing import Any

import yaml

from untaped.capabilities.awx.application.secret_paths import replace_at, values_at
from untaped.capabilities.awx.domain import ApplyOutcome, FieldChange, ResourceSpec

REDACTED = "<redacted>"


def semantic_equal(
    left: Any,
    right: Any,
    *,
    allow_server_enrichment: bool = False,
    structured_text: bool = False,
) -> bool:
    """Compare user-owned values exactly, with explicit enrichment allowance."""
    if structured_text:
        left = _parse_structured_string(left)
        right = _parse_structured_string(right)
    if allow_server_enrichment and isinstance(left, Mapping) and isinstance(right, Mapping):
        if not left:
            # An empty request is a clear (``survey_spec: {}``), not a subset.
            return not right
        return bool(
            all(
                key in right and semantic_equal(value, right[key], allow_server_enrichment=True)
                for key, value in left.items()
            )
        )
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return bool(
            set(left) == set(right) and all(semantic_equal(left[key], right[key]) for key in left)
        )
    if isinstance(left, list) and isinstance(right, list):
        return bool(
            len(left) == len(right)
            and all(
                semantic_equal(a, b, allow_server_enrichment=allow_server_enrichment)
                for a, b in zip(left, right, strict=True)
            )
        )
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    return bool(left == right)


def redact_value(value: Any, paths: Iterable[str], *, replacement: str = REDACTED) -> Any:
    """Deep-copy ``value`` and replace every known secret path."""
    result = copy.deepcopy(value)
    for path in paths:
        replace_at(result, path, replacement)
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


__all__ = [
    "REDACTED",
    "redact_field_change",
    "redact_outcome",
    "redact_value",
    "semantic_equal",
]


def _parse_structured_string(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    for parser in (json.loads, yaml.safe_load):
        try:
            parsed = parser(value)
        except ValueError, yaml.YAMLError:
            continue
        if isinstance(parsed, dict | list):
            return parsed
    return value


def redact_error(error: Exception, spec: ResourceSpec, *records: Any) -> str:
    """Remove known current and submitted secrets from controller error text."""
    message = str(error)
    values: set[str] = set()
    for record in records:
        for path in spec.secret_paths:
            for value in values_at(record, path):
                if isinstance(value, str) and value:
                    values.add(value)
                    values.add(json.dumps(value)[1:-1])
    for value in sorted(values, key=len, reverse=True):
        message = message.replace(value, REDACTED)
    return message
