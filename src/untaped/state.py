"""Typed CRUD over a tool's state section in ``~/.untaped/config.yml``.

The shared config file is co-owned by every untaped tool, so all writes ride
:func:`untaped.config_file.mutate_tool_state` (section-scoped, locked,
atomic). ``StateCollection`` is a list of records keyed by an id field
(workspace registry shape); ``StateMap`` is a flat ``str → str`` map
(ansible aliases shape). Both drop their key when emptied so the enclosing
section can collapse.
"""

from __future__ import annotations

from collections.abc import Mapping
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
        ident = record.get(self._id_field)
        if not isinstance(ident, str) or not ident:
            raise ConfigError(
                f"state record for `{self._section}.{self._key}` must include {self._id_field!r}"
            )

        def _apply(state: dict[str, Any]) -> None:
            rows = [row for row in self._rows(state) if row.get(self._id_field) != ident]
            rows.append(dict(record))
            state[self._key] = rows

        mutate_tool_state(self._section, _apply)

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


class StateMap:
    """A flat ``str → str`` tool-state map."""

    def __init__(self, section: str, key: str) -> None:
        self._section = section
        self._key = key

    def entries(self) -> dict[str, str]:
        """The whole map; ``{}`` when unset or malformed rows are non-str."""
        raw = read_tool_state(self._section).get(self._key)
        if not isinstance(raw, dict):
            return {}
        return {str(k): str(v) for k, v in raw.items()}

    def get(self, key: str) -> str | None:
        return self.entries().get(key)

    def set(self, key: str, value: str) -> None:
        def _apply(state: dict[str, Any]) -> None:
            raw = state.get(self._key)
            mapping = dict(raw) if isinstance(raw, dict) else {}
            mapping[key] = value
            state[self._key] = mapping

        mutate_tool_state(self._section, _apply)

    def remove(self, key: str) -> bool:
        removed = False

        def _apply(state: dict[str, Any]) -> None:
            nonlocal removed
            raw = state.get(self._key)
            mapping = dict(raw) if isinstance(raw, dict) else {}
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
