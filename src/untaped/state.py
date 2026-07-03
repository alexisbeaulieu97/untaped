"""Typed CRUD over a tool's state section in ``~/.untaped/config.yml``.

The shared config file is co-owned by every untaped tool, so all writes ride
:func:`untaped.config_file.mutate_tool_state` (section-scoped, locked,
atomic). ``StateCollection`` is a list of records keyed by an id field
(workspace registry shape); ``StateMap`` is a flat ``str → str`` map
(ansible aliases shape). Both drop their key when emptied so the enclosing
section can collapse.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from untaped.config_file import mutate_tool_state, read_tool_state
from untaped.errors import ConfigError


class StateCollection:
    """A list-of-records tool-state collection keyed by ``id_field``."""

    def __init__(self, section: str, key: str, *, id_field: str = "name") -> None:
        self._section = section
        self._key = key
        self._id_field = id_field

    def entries(self) -> list[dict[str, Any]]:
        """All records; ``[]`` when unset. Malformed state raises ConfigError."""
        return self._rows(read_tool_state(self._section))

    def get(self, ident: str) -> dict[str, Any] | None:
        """The record whose id field equals ``ident``, or ``None``."""
        for row in self.entries():
            if row.get(self._id_field) == ident:
                return row
        return None

    def upsert(self, record: Mapping[str, Any]) -> None:
        """Insert ``record``, replacing any existing record with the same id."""
        ident = self._record_id(record)
        self.mutate(
            lambda rows: [
                *[row for row in rows if row.get(self._id_field) != ident],
                dict(record),
            ]
        )

    def insert(self, record: Mapping[str, Any]) -> None:
        """Insert ``record``; raise ConfigError when the id already exists."""
        ident = self._record_id(record)

        def _replace(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            if any(row.get(self._id_field) == ident for row in rows):
                raise ConfigError(
                    f"state record {ident!r} already exists in `{self._section}.{self._key}`"
                )
            return [*rows, dict(record)]

        self.mutate(_replace)

    def mutate(
        self, fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        """Replace rows under the config-file lock and return the replacement."""
        replacement: list[dict[str, Any]] = []

        def _apply(state: dict[str, Any]) -> None:
            nonlocal replacement
            replacement = self._validate_rows(fn(self._rows(state)))
            if replacement:
                state[self._key] = [dict(row) for row in replacement]
            else:
                state.pop(self._key, None)

        mutate_tool_state(self._section, _apply)
        return [dict(row) for row in replacement]

    def remove(self, ident: str) -> bool:
        """Remove the record with id ``ident``; report whether one existed."""
        removed = False

        def _apply(state: dict[str, Any]) -> None:
            nonlocal removed
            rows = self._rows(state)
            kept = [row for row in rows if row.get(self._id_field) != ident]
            removed = len(kept) != len(rows)
            if not removed:
                return
            if kept:
                state[self._key] = kept
            else:
                state.pop(self._key, None)

        mutate_tool_state(self._section, _apply)
        return removed

    def _rows(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        if self._key not in state:
            return []
        raw = state.get(self._key)
        if not isinstance(raw, list) or not all(isinstance(row, dict) for row in raw):
            raise ConfigError(
                f"invalid state: `{self._section}.{self._key}` must be a list of mappings"
            )
        return [dict(row) for row in raw]

    def _record_id(self, record: Mapping[str, Any]) -> str:
        ident = record.get(self._id_field)
        if not isinstance(ident, str) or not ident:
            raise ConfigError(
                f"state record for `{self._section}.{self._key}` must include {self._id_field!r}"
            )
        return ident

    def _validate_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ConfigError(
                f"invalid state mutation: `{self._section}.{self._key}` replacement "
                "must be a list of mappings"
            )
        return [dict(row) for row in rows]


class StateMap:
    """A flat ``str → str`` tool-state map."""

    def __init__(self, section: str, key: str) -> None:
        self._section = section
        self._key = key

    def entries(self) -> dict[str, str]:
        """The whole map; ``{}`` when unset. Malformed state raises ConfigError."""
        return self._mapping(read_tool_state(self._section))

    def get(self, key: str) -> str | None:
        return self.entries().get(key)

    def set(self, key: str, value: str) -> None:
        def _apply(state: dict[str, Any]) -> None:
            mapping = self._mapping(state)
            mapping[key] = value
            state[self._key] = mapping

        mutate_tool_state(self._section, _apply)

    def remove(self, key: str) -> bool:
        removed = False

        def _apply(state: dict[str, Any]) -> None:
            nonlocal removed
            mapping = self._mapping(state)
            if key not in mapping:
                return
            del mapping[key]
            removed = True
            if mapping:
                state[self._key] = mapping
            else:
                state.pop(self._key, None)

        mutate_tool_state(self._section, _apply)
        return removed

    def _mapping(self, state: dict[str, Any]) -> dict[str, str]:
        if self._key not in state:
            return {}
        raw = state.get(self._key)
        if not isinstance(raw, dict):
            raise ConfigError(f"invalid state: `{self._section}.{self._key}` must be a mapping")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in raw.items()):
            raise ConfigError(f"invalid state: `{self._section}.{self._key}` must be a string map")
        return dict(raw)
