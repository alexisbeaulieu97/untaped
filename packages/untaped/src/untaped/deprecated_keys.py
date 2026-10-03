"""Renamed, retired and deprecated config keys of a settings section.

A section model declares three ClassVars, each mapping dotted paths relative
to the section:

- ``renamed_keys`` (``{"old": "new"}``): the old key is still read, as the
  new one, with a warning, until the next major release;
- ``retired_keys`` (same shape): the old key is no longer read, but
  ``config migrate`` still renames it;
- ``deprecated_settings`` (``{"field": "message"}``): a current field that is
  still read with its old meaning, with a warning carrying the message.

This module is the one home for the rules those declarations follow, for
rewriting one layer of section data to current names, and for the
once-per-process warnings.
"""

from __future__ import annotations

import copy
import types
import typing
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from typing import Any, Literal

from pydantic import BaseModel

from untaped.config_schema import walk_settings
from untaped.errors import ConfigError
from untaped.messages import deprecated_message

DECLARATIONS = ("renamed_keys", "retired_keys", "deprecated_settings")

KeyUseKind = Literal["renamed", "ignored", "retired", "deprecated"]


@dataclass(frozen=True)
class KeyMappings:
    """The validated key declarations of one section model."""

    readable: Mapping[str, str]
    """Old key → current field, for each old key that is still read."""

    migratable: Mapping[str, str]
    """Every renamed or retired key → current field."""

    retired: frozenset[str]
    """Old keys that are no longer read."""

    distance: Mapping[str, int]
    """Hops from each old key to its current field (the closest hop wins)."""

    deprecated: Mapping[str, str]
    """Current field → message, for each deprecated setting."""

    def __bool__(self) -> bool:
        return bool(self.migratable or self.deprecated)


@dataclass(frozen=True)
class KeyUse:
    """One old key or deprecated setting found in a layer of section data."""

    old: str
    """The key as found (relative to the section)."""

    new: str
    """The current field it maps to (the field itself for a deprecated setting)."""

    kind: KeyUseKind
    kept: str | None = None
    """For an ``ignored`` key: the spelling whose value was kept."""

    message: str | None = None
    """For a ``deprecated`` setting: its declared message."""


_EMPTY = KeyMappings({}, {}, frozenset(), {}, {})


def _declared(model: type[BaseModel], name: str) -> object:
    return getattr(model, name, {})


def _nested_models(model: type[BaseModel]) -> list[type[BaseModel]]:
    """Every ``BaseModel`` class reachable through ``model``'s fields."""
    found: list[type[BaseModel]] = []
    pending = [model]
    while pending:
        current = pending.pop()
        for field in current.model_fields.values():
            for candidate in _model_args(field.annotation):
                if candidate not in found and candidate is not model:
                    found.append(candidate)
                    pending.append(candidate)
    return found


def _model_args(annotation: Any) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        return [arg for arg in typing.get_args(annotation) if _is_model(arg)]
    return []


def _is_model(value: object) -> bool:
    return isinstance(value, type) and issubclass(value, BaseModel)


def mapping_errors(model: type[BaseModel]) -> list[str]:
    """One sentence per broken declaration rule of ``model``; ``[]`` when valid."""
    errors: list[str] = []
    declared: dict[str, Mapping[str, str]] = {}
    for name in DECLARATIONS:
        value = _declared(model, name)
        if not isinstance(value, Mapping) or not all(
            isinstance(key, str) and key and isinstance(item, str) and item
            for key, item in value.items()
        ):
            errors.append(f"{name} must map non-empty strings to non-empty strings")
            value = {}
        declared[name] = value
    errors.extend(
        f"{name} is declared on nested model {nested.__name__}; "
        "declare it on the section model with dotted paths"
        for nested in _nested_models(model)
        for name in DECLARATIONS
        if _declared(nested, name)
    )
    leaves = {descriptor.key for descriptor in walk_settings(model, include_collections=True)}
    errors.extend(_mapping_errors(declared["renamed_keys"], declared["retired_keys"], leaves))
    for key, message in sorted(declared["deprecated_settings"].items()):
        if key not in leaves:
            errors.append(f"deprecated setting {key!r} is not a setting")
        elif not message.strip():
            errors.append(f"deprecated setting {key!r} needs a message")
    return errors


def _mapping_errors(
    renamed: Mapping[str, str], retired: Mapping[str, str], leaves: set[str]
) -> list[str]:
    """The chain and collision rules of ``renamed_keys`` and ``retired_keys``."""
    errors = [
        f"{key!r} is in both renamed_keys and retired_keys"
        for key in sorted(renamed.keys() & retired.keys())
    ]
    for key in sorted({*renamed, *retired}):
        clash = _clashing_leaf(key, leaves)
        if clash is not None:
            errors.append(f"old key {key!r} clashes with the current setting {clash!r}")
    for key, target in sorted(renamed.items()):
        if target in retired:
            errors.append(f"renamed key {key!r} points at the retired key {target!r}")
        elif target not in leaves and target not in renamed:
            errors.append(f"renamed key {key!r} points at {target!r}, which is not a setting")
    for key, target in sorted(retired.items()):
        if target not in leaves and target not in renamed and target not in retired:
            errors.append(f"retired key {key!r} points at {target!r}, which is not a setting")
    mapped = {**retired, **renamed}
    errors.extend(
        f"the rename chain from {key!r} is a cycle"
        for key in sorted(mapped)
        if _chain_end(key, mapped) is None
    )
    return errors


def _clashing_leaf(key: str, leaves: set[str]) -> str | None:
    """The current setting ``key`` sits at, above or below, if any."""
    for leaf in sorted(leaves):
        if key == leaf or key.startswith(f"{leaf}.") or leaf.startswith(f"{key}."):
            return leaf
    return None


def _chain_end(key: str, mapped: Mapping[str, str]) -> tuple[str, int] | None:
    """``(current field, hops)`` at the end of ``key``'s chain; ``None`` on a cycle."""
    seen = {key}
    current, hops = mapped[key], 1
    while current in mapped:
        if current in seen:
            return None
        seen.add(current)
        current, hops = mapped[current], hops + 1
    return current, hops


@cache
def key_mappings(model: type[BaseModel]) -> KeyMappings:
    """The validated declarations of section ``model`` (cached per class).

    Raises :class:`ConfigError` naming the first broken rule.
    """
    errors = mapping_errors(model)
    if errors:
        raise ConfigError(f"invalid key declarations on {model.__name__}: {errors[0]}")
    if not any(_declared(model, name) for name in DECLARATIONS):
        return _EMPTY
    renamed: Mapping[str, str] = _declared(model, "renamed_keys")  # type: ignore[assignment]
    retired: Mapping[str, str] = _declared(model, "retired_keys")  # type: ignore[assignment]
    deprecated: Mapping[str, str] = _declared(model, "deprecated_settings")  # type: ignore[assignment]
    mapped = {**retired, **renamed}
    ends = {key: _chain_end(key, mapped) for key in mapped}
    migratable = {key: end[0] for key, end in ends.items() if end is not None}
    return KeyMappings(
        readable={key: migratable[key] for key in renamed},
        migratable=migratable,
        retired=frozenset(retired),
        distance={key: end[1] for key, end in ends.items() if end is not None},
        deprecated=dict(deprecated),
    )


_MISSING = object()


def _lookup(data: Mapping[str, Any], path: str) -> Any:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _pop(data: dict[str, Any], path: str) -> None:
    """Remove ``path`` from ``data``, dropping parents it leaves empty."""
    parts = path.split(".")
    parents = [data]
    for part in parts[:-1]:
        parents.append(parents[-1][part])
    del parents[-1][parts[-1]]
    for depth in range(len(parts) - 1, 0, -1):
        if parents[depth]:
            break
        del parents[depth - 1][parts[depth - 1]]


def _place(data: dict[str, Any], path: str, value: Any) -> bool:
    """Set ``path`` in ``data``, creating parents; ``False`` when a parent is not a mapping."""
    parts = path.split(".")
    node = data
    for part in parts[:-1]:
        child = node.setdefault(part, {})
        if not isinstance(child, dict):
            return False
        node = child
    node[parts[-1]] = value
    return True


def rename_keys(
    model: type[BaseModel], data: Mapping[str, Any]
) -> tuple[dict[str, Any], tuple[KeyUse, ...]]:
    """Rewrite one layer of section data to current names.

    Returns a copy of ``data`` with every readable old key moved to its
    current field, plus every old key and deprecated setting it found. In
    one layer the current key wins over an old spelling, and among old
    spellings the hop closest to the current name wins; the losers are
    dropped and reported ``ignored``. Retired keys stay in place (they are
    not read) and are reported ``retired``.
    """
    mappings = key_mappings(model)
    if not mappings:
        return dict(data), ()
    result = copy.deepcopy(dict(data))
    uses: list[KeyUse] = []
    found: dict[str, list[str]] = {}
    for old, new in mappings.readable.items():
        if _lookup(result, old) is not _MISSING:
            found.setdefault(new, []).append(old)
    for new, olds in sorted(found.items()):
        ranked = sorted(olds, key=lambda old: (mappings.distance[old], old))
        keeper = new if _lookup(result, new) is not _MISSING else ranked[0]
        for old in ranked:
            if old == keeper:
                value = _lookup(result, old)
                _pop(result, old)
                if not _place(result, new, value):
                    _place(result, old, value)
                    continue
                uses.append(KeyUse(old, new, "renamed"))
            else:
                _pop(result, old)
                uses.append(KeyUse(old, new, "ignored", kept=keeper))
    for old in sorted(mappings.retired):
        if _lookup(result, old) is not _MISSING:
            uses.append(KeyUse(old, mappings.migratable[old], "retired"))
    for key, message in sorted(mappings.deprecated.items()):
        if _lookup(result, key) is not _MISSING:
            uses.append(KeyUse(key, key, "deprecated", message=message))
    return result, tuple(uses)


_warned: set[str] = set()


def warn_once(message: str) -> None:
    """Print ``message`` as a warning, once per process per text."""
    if message in _warned:
        return
    _warned.add(message)
    from untaped.ui import ui_context  # noqa: PLC0415 - keep settings imports light

    ui_context(strict=False).message("warning", message)


def reset_key_warnings() -> None:
    """Forget which deprecation warnings were printed (tests, embedding)."""
    _warned.clear()


def use_warning(use: KeyUse, *, old: str, new: str, kept: str | None = None) -> str | None:
    """The warning for ``use``, with keys spelled as the caller names them.

    ``old``/``new``/``kept`` are the user-facing spellings (``github.corpus_path``
    or ``UNTAPED_GITHUB__CORPUS_PATH``); retired keys get no read-time warning.
    """
    if use.kind == "renamed":
        return deprecated_message(old, new)
    if use.kind == "ignored":
        return f"{old} is deprecated and ignored because {kept} is also set; use {new}"
    if use.kind == "deprecated":
        return f"{old} is deprecated and will be removed in the next major release; {use.message}"
    return None


__all__ = [
    "KeyMappings",
    "KeyUse",
    "key_mappings",
    "mapping_errors",
    "rename_keys",
    "reset_key_warnings",
    "use_warning",
    "warn_once",
]
