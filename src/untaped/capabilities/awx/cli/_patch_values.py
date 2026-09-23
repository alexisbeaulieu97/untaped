"""Parse top-level field replacements for ``patch``.

The selection path patches every listed item with a common set of fields. Those
fields come from two sources, merged with ``--set`` winning:

- ``--patch-file PATH`` — a partial-spec YAML mapping (the same field names a
  saved resource's ``spec:`` block uses).
- ``--set NAME=VALUE`` (repeatable) — imperative, coerced so types reach AWX
  correctly (``verbosity=2`` → ``2``, ``enabled=true`` → ``True``). When the
  target record is known, a field it holds as a string stays a string
  (``scm_branch=1.10`` stays ``"1.10"``) unless the value is a JSON object or
  array.

The merged patch becomes a synthetic ``Resource.spec`` that flows through the
normal apply pipeline (FK resolution, diff, sparse PATCH).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from untaped.api import parse_kv_pairs, read_structured_file


def parse_set_pairs(
    values: list[str] | None, *, record: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Parse ``--set KEY=VALUE`` entries, coercing each value.

    Splits on the first ``=`` (via the SDK's :func:`parse_kv_pairs`, which
    rejects malformed entries up front), then coerces the value with
    :func:`json.loads` so numbers, booleans, ``null`` and structured JSON land
    as the right type. A value that is not valid JSON is kept as a plain string
    (so ``job_tags=deploy`` → ``"deploy"`` without needing quotes). When
    ``record`` holds the field as a string, only a JSON object/array is decoded.
    """
    raw = parse_kv_pairs(values, flag="--set")
    existing = record or {}
    return {
        key: _coerce(value, string_field=isinstance(existing.get(key), str))
        for key, value in raw.items()
    }


def _coerce(value: str, *, string_field: bool = False) -> Any:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return value
    if string_field and not isinstance(decoded, dict | list):
        return value
    return decoded


def build_patch(set_pairs: list[str] | None, patch_file: Path | None) -> dict[str, Any]:
    """Merge ``--patch-file`` then ``--set`` (``--set`` wins on key clash)."""
    overlay: dict[str, Any] = {}
    if patch_file is not None:
        overlay.update(read_structured_file(patch_file.expanduser()))
    overlay.update(parse_set_pairs(set_pairs))
    return overlay


__all__ = ["build_patch", "parse_set_pairs"]
