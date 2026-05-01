"""Spec-driven matching of ``$encrypted$`` placeholders in apply payloads.

Patterns use dot notation with ``*`` matching any list element or dict
key:

- ``webhook_key``                — exact top-level key
- ``inputs.*``                   — any direct child of ``inputs``
- ``survey_spec.spec.*.default`` — ``default`` key on any list element
                                   under ``survey_spec.spec``

The walker drops matched ``$encrypted$`` values from the payload and
returns the dotted paths that were preserved, plus any
``$encrypted$`` literals it found at *undeclared* paths (a paranoid
safety net — the caller emits a warning).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from untaped_awx.domain import ResourceSpec

PLACEHOLDER = "$encrypted$"


def strip_encrypted(payload: dict[str, Any], spec: ResourceSpec) -> tuple[list[str], list[str]]:
    """Mutate ``payload`` to drop ``$encrypted$`` placeholders.

    Returns ``(preserved, dropped_undeclared)`` — both lists of dotted
    paths. ``preserved`` is the declared-and-stripped set; the rest are
    extras the caller should warn about.
    """
    preserved: list[str] = []
    dropped: list[str] = []
    _walk(payload, [], spec, preserved, dropped)
    return preserved, dropped


def _walk(
    obj: Any,
    path: list[str],
    spec: ResourceSpec,
    preserved: list[str],
    dropped: list[str],
) -> None:
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            value = obj[key]
            child_path = [*path, key]
            if isinstance(value, str) and value == PLACEHOLDER:
                rendered = ".".join(child_path)
                if _is_declared(child_path, spec):
                    preserved.append(rendered)
                else:
                    dropped.append(rendered)
                obj.pop(key)
            elif isinstance(value, (dict, list)):
                _walk(value, child_path, spec, preserved, dropped)
    elif isinstance(obj, list):
        for item in obj:
            _walk(item, [*path, "*"], spec, preserved, dropped)


def _is_declared(path_parts: list[str], spec: ResourceSpec) -> bool:
    return any(_pattern_matches(path_parts, p) for p in spec.secret_paths)


def _pattern_matches(path_parts: list[str], pattern: str) -> bool:
    pattern_parts = pattern.split(".")
    if len(pattern_parts) != len(path_parts):
        return False
    for p, pp in zip(pattern_parts, path_parts, strict=True):
        if p == "*":
            continue
        if p != pp:
            return False
    return True


WarnFn = Callable[[str], None]
