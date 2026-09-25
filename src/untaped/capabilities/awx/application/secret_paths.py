"""Secret-path patterns: the one walker and ``$encrypted$`` placeholder stripping.

``ResourceSpec.secret_paths`` patterns use dot notation with ``*`` matching
any list element or dict key, and ``*[key=value]`` matching only the elements
that are mappings whose ``key`` equals ``value``; :func:`path_slots` is the
single walker every read, redact, strip, and remove of a secret path goes
through:

- ``webhook_key``                — exact top-level key
- ``inputs.*``                   — any direct child of ``inputs``
- ``survey_spec.spec.*[type=password].default`` — ``default`` key on the
                                   password questions under ``survey_spec.spec``

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
    wildcard, predicate = _wildcard(head)
    keys: list[Any]
    if isinstance(value, Mapping):
        keys = list(value) if wildcard else [head] if head in value else []
    elif isinstance(value, list | tuple) and wildcard:
        keys = list(range(len(value)))
    else:
        return
    if predicate is not None:
        field, expected = predicate
        keys = [
            key
            for key in keys
            if isinstance(value[key], Mapping) and value[key].get(field) == expected
        ]
    for key in keys:
        if rest:
            yield from path_slots(value[key], rest)
        else:
            yield value, key


def _wildcard(segment: str) -> tuple[bool, tuple[str, str] | None]:
    """Split a pattern segment into (is wildcard, optional ``key=value`` filter)."""
    if segment == "*":
        return True, None
    if segment.startswith("*[") and segment.endswith("]") and "=" in segment:
        field, _, expected = segment[2:-1].partition("=")
        return True, (field, expected)
    return False, None


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
    declared: dict[tuple[int, Any], str] = {}
    for pattern in spec.secret_paths:
        for container, key in path_slots(payload, pattern):
            declared.setdefault((id(container), key), pattern)
    preserved: list[str] = []
    dropped: list[str] = []
    _walk(payload, [], declared, preserved, dropped)
    return preserved, dropped


def _walk(
    obj: Any,
    path: list[str],
    declared: Mapping[tuple[int, Any], str],
    preserved: list[str],
    dropped: list[str],
) -> None:
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            value = obj[key]
            child_path = [*path, key]
            if isinstance(value, str) and value == PLACEHOLDER:
                pattern = declared.get((id(obj), key))
                if pattern is not None:
                    preserved.append(_render(child_path, pattern))
                else:
                    dropped.append(".".join(child_path))
                obj.pop(key)
            elif isinstance(value, (dict, list)):
                _walk(value, child_path, declared, preserved, dropped)
    elif isinstance(obj, list):
        for item in obj:
            _walk(item, [*path, "*"], declared, preserved, dropped)


def _render(path_parts: list[str], pattern: str) -> str:
    """Name a preserved slot, keeping the pattern's filtered wildcards.

    The rendered path is later replayed with :func:`remove_at` against the
    existing record, so a ``*[key=value]`` segment must survive; a plain
    ``*`` or literal key renders as walked.
    """
    return ".".join(
        segment if _wildcard(segment)[1] is not None else part
        for part, segment in zip(path_parts, pattern.split("."), strict=True)
    )
