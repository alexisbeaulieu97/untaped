"""Renamed, retired and deprecated config keys of a settings section.

A section model declares two ClassVars, each mapping dotted paths relative
to the section:

- ``renamed_keys`` (``{"old": "new"}``): the old key is still read, as the
  new one, with a warning, until the next major release;
- ``retired_keys`` (same shape): the old key is no longer read, but
  ``config migrate`` still renames it. A :class:`Retired` value instead of a
  target retires a key nothing in its own section replaces: its ``note``
  says why and what to do instead, and ``config migrate`` deletes the key,
  printing the value it held. A chain may end at such a key.

A current field that is still read with its old meaning is marked on the
field instead: ``Annotated[T, deprecated(replacement="use X")]`` (see
:mod:`untaped.stability`); it is read with a warning that names the
replacement.

This module is the one home for the rules those declarations follow, for
rewriting one layer of section data to current names, and for the
once-per-process warnings.
"""

from __future__ import annotations

import copy
import types
import typing
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Literal

from pydantic import BaseModel

from untaped.config_schema import unwrap_optional, walk_settings
from untaped.errors import ConfigError
from untaped.messages import deprecated_message
from untaped.profile_resolver import DEFAULT_PROFILE
from untaped.stability import Deprecated, Stability, field_marks, mark_errors, replacement_text

DECLARATIONS = ("renamed_keys", "retired_keys")

KeyUseKind = Literal["renamed", "ignored", "retired", "deprecated"]


@dataclass(frozen=True, slots=True)
class Retired:
    """A ``retired_keys`` value for a key with no successor in its own section.

    ``note`` is data, printed by doctor and ``config migrate``: why the key
    went and what to set instead (``deleted in 11.0; the repo store lives
    under git.store_dir``).
    """

    note: str


@dataclass(frozen=True, slots=True)
class DeletedKey:
    """An old key ``config migrate`` deletes: its :class:`Retired` note, and the hops to it."""

    note: str
    via: tuple[str, ...] = ()
    """The keys between the old key and the :class:`Retired` one, that one included."""

    def reason(self) -> str:
        """The note, naming the retired key it was reached through after its first clause.

        ``deleted in 11.0; the repo store …`` reached through ``cache_dir``
        reads ``deleted in 11.0 (via cache_dir); the repo store …``.
        """
        if not self.via:
            return self.note
        head, sep, tail = self.note.partition("; ")
        return f"{head} (via {self.via[-1]}){sep}{tail}"


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

    deprecated: Mapping[str, str | None]
    """Current field → replacement text (``None`` when it has none), for each deprecated setting."""

    deleted: Mapping[str, DeletedKey] = field(default_factory=dict)
    """Old keys whose chain ends at a :class:`Retired` key (that key included)."""

    def __bool__(self) -> bool:
        return bool(self.migratable or self.deprecated or self.deleted)


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
    """For a ``deprecated`` setting: its replacement text, if it names one."""


NO_KEY_MAPPINGS = KeyMappings({}, {}, frozenset(), {}, {})


def _declared(model: type[BaseModel], name: str) -> object:
    return getattr(model, name, {})


def _valid(model: type[BaseModel], name: str) -> Mapping[str, str]:
    """The string targets of a declaration ``mapping_errors`` has already checked."""
    declared = typing.cast(Mapping[str, object], _declared(model, name))
    return {key: value for key, value in declared.items() if isinstance(value, str)}


def _retired_notes(model: type[BaseModel]) -> Mapping[str, str]:
    """The ``retired_keys`` declared with a :class:`Retired` value, key → note."""
    declared = typing.cast(Mapping[str, object], _declared(model, "retired_keys"))
    return {key: value.note for key, value in declared.items() if isinstance(value, Retired)}


def _valid_value(name: str, value: object) -> bool:
    if isinstance(value, str):
        return bool(value)
    return name == "retired_keys" and isinstance(value, Retired) and bool(value.note.strip())


def _nested_models(model: type[BaseModel]) -> list[type[BaseModel]]:
    """Every ``BaseModel`` class reachable through ``model``'s fields."""
    found: list[type[BaseModel]] = []
    pending = [model]
    while pending:
        current = pending.pop()
        for info in current.model_fields.values():
            for candidate in _model_args(info.annotation):
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
    declared: dict[str, Mapping[str, object]] = {}
    for name in DECLARATIONS:
        value = _declared(model, name)
        if not isinstance(value, Mapping) or not all(
            isinstance(key, str) and key and _valid_value(name, item) for key, item in value.items()
        ):
            target = (
                "non-empty strings or Retired(note=)"
                if name == "retired_keys"
                else ("non-empty strings")
            )
            errors.append(f"{name} must map non-empty strings to {target}")
            value = {}
        declared[name] = value
    errors.extend(
        f"{name} is declared on nested model {nested.__name__}; "
        "declare it on the section model with dotted paths"
        for nested in _nested_models(model)
        for name in DECLARATIONS
        if _declared(nested, name)
    )
    errors.extend(mark_errors(model))
    if not any(declared.values()):
        return errors
    leaves = set(_leaf_paths(model))
    errors.extend(_mapping_errors(declared["renamed_keys"], declared["retired_keys"], leaves))
    return errors


def _leaf_paths(model: type[BaseModel], prefix: str = "") -> list[str]:
    """Every setting path of ``model``, as ``walk_settings`` finds them, without defaults.

    Evaluating a ``default_factory`` here could raise, and these checks run
    while composing every plugin.
    """
    paths: list[str] = []
    for name, info in model.model_fields.items():
        annotation = unwrap_optional(info.annotation)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            paths.extend(_leaf_paths(annotation, f"{prefix}{name}."))
        else:
            paths.append(f"{prefix}{name}")
    return paths


def _mapping_errors(
    renamed_declared: Mapping[str, object],
    retired_declared: Mapping[str, object],
    leaves: set[str],
) -> list[str]:
    """The chain and collision rules of ``renamed_keys`` and ``retired_keys``.

    A :class:`Retired` value ends a chain: a retired key may point at it.
    """
    renamed = typing.cast(Mapping[str, str], renamed_declared)
    retired = {key: value for key, value in retired_declared.items() if isinstance(value, str)}
    ends = {key for key, value in retired_declared.items() if isinstance(value, Retired)}
    errors = [
        f"{key!r} is in both renamed_keys and retired_keys"
        for key in sorted(renamed.keys() & retired_declared.keys())
    ]
    old_keys = sorted({*renamed, *retired_declared})
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
        if target in retired or target in ends:
            errors.append(f"renamed key {key!r} points at the retired key {target!r}")
        elif target not in leaves and target not in renamed:
            errors.append(f"renamed key {key!r} points at {target!r}, which is not a setting")
    for key, target in sorted(retired.items()):
        if (
            target not in leaves
            and target not in renamed
            and target not in retired
            and target not in ends
        ):
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
    deprecated = {
        path: (mark.replacement if isinstance(mark.replacement, str) else None)
        for path, mark in field_marks(model).items()
        if isinstance(mark, Deprecated)
    }
    if not deprecated and not any(_declared(model, name) for name in DECLARATIONS):
        return NO_KEY_MAPPINGS
    renamed = _valid(model, "renamed_keys")
    retired = _valid(model, "retired_keys")
    notes = _retired_notes(model)
    mapped = {**retired, **renamed}
    deleted = {key: DeletedKey(note) for key, note in notes.items()}
    live: dict[str, tuple[str, int]] = {}
    for key in mapped:
        path = _chain(key, mapped)
        if path is None:
            continue
        if path[-1] in notes:
            deleted[key] = DeletedKey(notes[path[-1]], via=tuple(path[1:]))
        else:
            live[key] = (path[-1], len(path) - 1)
    migratable = {key: end[0] for key, end in live.items()}
    return KeyMappings(
        readable={key: migratable[key] for key in renamed if key in migratable},
        migratable=migratable,
        retired=frozenset(key for key in retired if key in migratable),
        distance={key: end[1] for key, end in live.items()},
        deprecated=deprecated,
        deleted=deleted,
    )


def _chain(key: str, mapped: Mapping[str, str]) -> list[str] | None:
    """``key`` and every hop after it to the chain's end; ``None`` on a cycle."""
    path = [key]
    while path[-1] in mapped:
        nxt = mapped[path[-1]]
        if nxt in path:
            return None
        path.append(nxt)
    return path


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
    new: str | None
    """The current field; ``None`` for a deprecated setting, which keeps its name."""

    kind: Literal["renamed", "retired", "deleted", "deprecated"]
    message: str | None = None
    """For a deprecated setting: its replacement text, if it names one; for a
    deleted key: its :class:`Retired` note."""


def profile_sections(
    raw: Mapping[str, Any], sections: Mapping[str, type[BaseModel]]
) -> Iterator[tuple[str, str, type[BaseModel], dict[str, Any]]]:
    """``(profile, section, model, section data)`` for every section set in every profile.

    Profiles come ``default`` first, then by name. The section data is the
    mapping in ``raw`` itself, so a caller may change it in place.
    """
    profiles = raw.get("profiles")
    if not isinstance(profiles, dict):
        return
    for name in sorted(profiles, key=lambda name: (name != DEFAULT_PROFILE, str(name))):
        data = profiles[name]
        if not isinstance(data, dict):
            continue
        for section, model in sections.items():
            section_data = data.get(section)
            if isinstance(section_data, dict):
                yield str(name), section, model, section_data


def retired_values(section: str, key: str) -> tuple[object, ...]:
    """The values config.yml still sets for ``<section>.<key>``, distinct, ``default``'s first.

    For a deleted key (a :class:`Retired` entry): no settings model reads
    it, but a plugin may need the old value once, as ``setup migrate-dirs``
    needs a custom root a deleted ``cache_dir`` named. ``key`` may be dotted.
    Read from the file as written, every profile, never validated; empty
    once ``config migrate`` has deleted the key.
    """
    from untaped.config_file import read_config_dict  # noqa: PLC0415 - config_file imports settings

    profiles = read_config_dict().get("profiles")
    if not isinstance(profiles, Mapping):
        return ()
    found: list[object] = []
    for name in sorted(profiles, key=lambda name: (name != DEFAULT_PROFILE, str(name))):
        data = profiles[name]
        section_data = data.get(section) if isinstance(data, Mapping) else None
        if not isinstance(section_data, Mapping):
            continue
        value = _lookup(section_data, key)
        if value is not _MISSING and value not in found:
            found.append(value)
    return tuple(found)


def scan_keys(
    raw: Mapping[str, Any],
    sections: Mapping[str, type[BaseModel]],
    stabilities: Mapping[str, Stability | None] | None = None,
) -> list[FoundKey]:
    """Every old key ``config migrate`` would move, and every deprecated setting, per profile.

    ``stabilities`` maps a section to its plugin's mark: every key set in
    the section of a deprecated plugin is reported as deprecated, with the
    plugin's replacement text.
    """
    found: list[FoundKey] = []
    for profile, section, model, data in profile_sections(raw, sections):
        mappings = key_mappings(model)
        for move in migration_moves(model, data):
            kind: Literal["renamed", "retired"] = (
                "retired" if move.old in mappings.retired else "renamed"
            )
            if move.action == "deleted":
                found.append(FoundKey(profile, section, move.old, None, "deleted", move.note))
                continue
            found.append(FoundKey(profile, section, move.old, mappings.migratable[move.old], kind))
        for key, message in sorted(mappings.deprecated.items()):
            if _lookup(data, key) is not _MISSING:
                found.append(FoundKey(profile, section, key, None, "deprecated", message))
        plugin = (stabilities or {}).get(section)
        if isinstance(plugin, Deprecated):
            reported = {
                item.old for item in found if (item.profile, item.section) == (profile, section)
            }
            use = replacement_text(plugin, None)
            keys = (".".join(d.path) for d in walk_settings(model, include_collections=True))
            found.extend(
                FoundKey(profile, section, key, None, "deprecated", use)
                for key in keys
                if key not in reported and _lookup(data, key) is not _MISSING
            )
    return found


@dataclass(frozen=True)
class KeyMove:
    """One step of ``config migrate`` in one section of one profile."""

    old: str
    to: str
    """Where the value lives afterwards: the current field, or for a drop the
    spelling that kept the value; empty for a delete."""

    action: Literal["renamed", "dropped", "deleted"]
    note: str | None = None
    """For a delete: why the key went, naming the hop it was reached through."""

    value: Any = None
    """For a delete: the value the key held, which goes with it."""


def migration_moves(model: type[BaseModel], data: Mapping[str, Any]) -> list[KeyMove]:
    """What ``config migrate`` does to one section's data in one profile.

    Every renamed or retired key moves to its current field; when the field
    or a closer old spelling is also set, the other spelling is dropped.
    Every key whose chain ends at a :class:`Retired` key is deleted.
    """
    mappings = key_mappings(model)
    moves = [
        KeyMove(old, "", "deleted", note=deleted.reason(), value=value)
        for old, deleted in sorted(mappings.deleted.items())
        if (value := _lookup(data, old)) is not _MISSING
    ]
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
    if move.action != "renamed" or _place(data, move.to, value):
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

    ``old``/``new``/``kept`` are the user-facing spellings (``demo.old_key``
    or ``UNTAPED_DEMO__OLD_KEY``); retired keys get no read-time warning.
    """
    if use.kind == "renamed":
        return deprecated_message(old, new)
    if use.kind == "ignored":
        advice = "" if kept == new else f"; use {new}"
        return f"{old} is deprecated and ignored because {kept} is also set{advice}"
    if use.kind == "deprecated":
        return deprecated_message(old, use.message)
    return None


__all__ = [
    "NO_KEY_MAPPINGS",
    "DeletedKey",
    "FoundKey",
    "KeyMappings",
    "KeyMove",
    "KeyUse",
    "Retired",
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
