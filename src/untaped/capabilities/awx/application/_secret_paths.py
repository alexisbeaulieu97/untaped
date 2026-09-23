"""Secret-path patterns: the one walker and ``$encrypted$`` placeholder stripping.

``ResourceSpec.secret_paths`` patterns use dot notation with ``*`` matching
any list element or dict key; :func:`path_slots` is the single walker every
read, redact, and remove of a secret path goes through:

- ``webhook_key``                — exact top-level key
- ``inputs.*``                   — any direct child of ``inputs``
- ``survey_spec.spec.*.default`` — ``default`` key on any list element
                                   under ``survey_spec.spec``

The walker drops matched ``$encrypted$`` values from the payload and
returns the dotted paths that were preserved, plus any
``$encrypted$`` literals it found at *undeclared* paths (a paranoid
safety net — the caller emits a warning).

The walker mutates its input — see :func:`strip_encrypted_in_place`
below. Callers that need the original payload must ``copy.deepcopy``
before invoking.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from untaped.capabilities.awx.domain import ResourceSpec

PLACEHOLDER = "$encrypted$"


def path_slots(value: Any, pattern: str) -> Iterator[tuple[Any, Any]]:
    """Yield ``(container, key)`` for every existing slot ``pattern`` names.

    ``*`` matches every key of a mapping or index of a list/tuple; a missing
    key or a non-container along the way yields nothing. Keys are listed
    before descending, so callers may assign through a yielded slot.
    """
    head, _, rest = pattern.partition(".")
    keys: list[Any]
    if isinstance(value, Mapping):
        keys = list(value) if head == "*" else [head] if head in value else []
    elif isinstance(value, list | tuple) and head == "*":
        keys = list(range(len(value)))
    else:
        return
    for key in keys:
        if rest:
            yield from path_slots(value[key], rest)
        else:
            yield value, key


def values_at(value: Any, pattern: str) -> Iterator[Any]:
    """Every value stored at a slot ``pattern`` names."""
    for container, key in path_slots(value, pattern):
        yield container[key]


def replace_at(value: Any, pattern: str, replacement: Any) -> None:
    """In place: overwrite every mutable slot ``pattern`` names."""
    for container, key in path_slots(value, pattern):
        if isinstance(container, dict | list):
            container[key] = replacement


def remove_at(value: Any, pattern: str) -> None:
    """In place: delete every mutable slot ``pattern`` names."""
    # Reverse order keeps list indices valid while deleting several.
    for container, key in reversed(list(path_slots(value, pattern))):
        if isinstance(container, dict | list):
            del container[key]


def strip_encrypted_in_place(
    payload: dict[str, Any], spec: ResourceSpec
) -> tuple[list[str], list[str]]:
    """In-place: mutate ``payload`` to drop ``$encrypted$`` placeholders.

    Callers must pass a copy if they need the original payload preserved
    (see :func:`copy.deepcopy`) — the ``_in_place`` suffix is a load-bearing
    signal, not decoration.

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
