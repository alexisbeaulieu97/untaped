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

from untaped.config_schema import unwrap_optional
from untaped.errors import ConfigError
from untaped.messages import deprecated_message
from untaped.profile_resolver import DEFAULT_PROFILE

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


NO_KEY_MAPPINGS = KeyMappings({}, {}, frozenset(), {}, {})


def _declared(model: type[BaseModel], name: str) -> object:
    return getattr(model, name, {})


def _valid(model: type[BaseModel], name: str) -> Mapping[str, str]:
    """A declaration ``mapping_errors`` has already checked."""
    return typing.cast(Mapping[str, str], _declared(model, name))


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
    if not any(declared.values()):
        return errors
    leaves = set(_leaf_paths(model))
    errors.extend(_mapping_errors(declared["renamed_keys"], declared["retired_keys"], leaves))
    for key, message in sorted(declared["deprecated_settings"].items()):
        if key not in leaves:
            errors.append(f"deprecated setting {key!r} is not a setting")
        elif not message.strip():
            errors.append(f"deprecated setting {key!r} needs a message")
    return errors


def _leaf_paths(model: type[BaseModel], prefix: str = "") -> list[str]:
    """Every setting path of ``model``, as ``walk_settings`` finds them, without defaults.

    Evaluating a ``default_factory`` here could raise, and these checks run
    while composing every capability.
    """
    paths: list[str] = []
    for name, field in model.model_fields.items():
        annotation = unwrap_optional(field.annotation)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            paths.extend(_leaf_paths(annotation, f"{prefix}{name}."))
        else:
            paths.append(f"{prefix}{name}")
    return paths


def _mapping_errors(
    renamed: Mapping[str, str], retired: Mapping[str, str], leaves: set[str]
) -> list[str]:
    """The chain and collision rules of ``renamed_keys`` and ``retired_keys``."""
    errors = [
        f"{key!r} is in both renamed_keys and retired_keys"
        for key in sorted(renamed.keys() & retired.keys())
    ]
    old_keys = sorted({*renamed, *retired})
    for key in old_keys:
        clash = _clashing_leaf(key, leaves)
        if clash is not None:
            errors.append(f"old key {key!r} clashes with the current setting {clash!r}")
    errors.extend(
        f"old key {key!r} is below the old key {parent!r}; an old name is never reused"
        for key in old_keys
        for parent in old_keys
        if key.startswith(f"{parent}.")
    )
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
        return NO_KEY_MAPPINGS
    renamed = _valid(model, "renamed_keys")
    retired = _valid(model, "retired_keys")
    deprecated = _valid(model, "deprecated_settings")
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


def _spellings(
    mappings: KeyMappings, old_keys: Mapping[str, str], data: Mapping[str, Any]
) -> list[tuple[str, str, list[str]]]:
    """``(field, keeper, old spellings)`` for each field with an old spelling in ``data``.

    The one rule the reader and ``config migrate`` share: the current key
    wins over an old spelling, and among old spellings the hop closest to
    the current name wins. The old spellings are closest first.
    """
    found: dict[str, list[str]] = {}
    for old, new in old_keys.items():
        if _lookup(data, old) is not _MISSING:
            found.setdefault(new, []).append(old)
    result = []
    for new, olds in sorted(found.items()):
        ranked = sorted(olds, key=lambda old: (mappings.distance[old], old))
        keeper = new if _lookup(data, new) is not _MISSING else ranked[0]
        result.append((new, keeper, ranked))
    return result


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
    for new, keeper, ranked in _spellings(mappings, mappings.readable, result):
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


@dataclass(frozen=True)
class FoundKey:
    """An old key or deprecated setting in one profile of the raw config."""

    profile: str
    section: str
    old: str
    new: str
    kind: Literal["renamed", "retired", "deprecated"]
    message: str | None = None


def scan_keys(raw: Mapping[str, Any], sections: Mapping[str, type[BaseModel]]) -> list[FoundKey]:
    """Every old key and deprecated setting in every profile (``default`` first)."""
    profiles = raw.get("profiles")
    if not isinstance(profiles, Mapping):
        return []
    found: list[FoundKey] = []
    for name in sorted(profiles, key=lambda name: (name != DEFAULT_PROFILE, str(name))):
        data = profiles[name]
        if not isinstance(data, Mapping):
            continue
        for section, model in sections.items():
            section_data = data.get(section)
            if not isinstance(section_data, Mapping):
                continue
            mappings = key_mappings(model)
            for old, new in sorted(mappings.migratable.items()):
                if _lookup(section_data, old) is not _MISSING:
                    kind: Literal["renamed", "retired"] = (
                        "retired" if old in mappings.retired else "renamed"
                    )
                    found.append(FoundKey(name, section, old, new, kind))
            for key, message in sorted(mappings.deprecated.items()):
                if _lookup(section_data, key) is not _MISSING:
                    found.append(FoundKey(name, section, key, key, "deprecated", message))
    return found


@dataclass(frozen=True)
class KeyMove:
    """One step of ``config migrate`` in one section of one profile."""

    old: str
    to: str
    """Where the value lives afterwards: the current field, or for a drop the
    spelling that kept the value."""

    action: Literal["renamed", "dropped"]


def migration_moves(model: type[BaseModel], data: Mapping[str, Any]) -> list[KeyMove]:
    """What ``config migrate`` does to one section's data in one profile.

    Every renamed or retired key moves to its current field; when the field
    or a closer old spelling is also set, the other spelling is dropped.
    """
    mappings = key_mappings(model)
    moves: list[KeyMove] = []
    for new, keeper, ranked in _spellings(mappings, mappings.migratable, data):
        for old in ranked:
            if old == keeper:
                moves.append(KeyMove(old, new, "renamed"))
            else:
                moves.append(KeyMove(old, keeper, "dropped"))
    return moves


def apply_move(data: dict[str, Any], move: KeyMove) -> bool:
    """Apply ``move`` to one section's data; ``False`` when it cannot be placed."""
    value = _lookup(data, move.old)
    _pop(data, move.old)
    if move.action == "dropped" or _place(data, move.to, value):
        return True
    _place(data, move.old, value)
    return False


def old_spellings(model: type[BaseModel], key: str) -> list[str]:
    """Every renamed or retired key of ``model`` that maps to field ``key``, closest first."""
    mappings = key_mappings(model)
    olds = [old for old, new in mappings.migratable.items() if new == key]
    return sorted(olds, key=lambda old: (mappings.distance[old], old))


_warned: set[str] = set()


def warn_once(message: str, *, key: str | None = None) -> None:
    """Print ``message`` as a warning, once per process per ``key`` (else per text).

    Callers warning about one old key in different words pass the same
    ``key``, so only the first of them prints.
    """
    seen = message if key is None else key
    if seen in _warned:
        return
    _warned.add(seen)
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
        advice = "" if kept == new else f"; use {new}"
        return f"{old} is deprecated and ignored because {kept} is also set{advice}"
    if use.kind == "deprecated":
        return f"{old} is deprecated and will be removed in the next major release; {use.message}"
    return None


__all__ = [
    "NO_KEY_MAPPINGS",
    "FoundKey",
    "KeyMappings",
    "KeyMove",
    "KeyUse",
    "apply_move",
    "key_mappings",
    "mapping_errors",
    "migration_moves",
    "old_spellings",
    "rename_keys",
    "reset_key_warnings",
    "scan_keys",
    "use_warning",
    "warn_once",
]
