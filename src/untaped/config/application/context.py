"""Per-tool config command context and key resolution."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from untaped.errors import ConfigError
from untaped.settings import Settings

if TYPE_CHECKING:
    from untaped.tool import ToolSpec


@dataclass(frozen=True)
class ToolConfigContext:
    """Tool identity needed to resolve user-facing config keys."""

    command: str
    section: str
    profile_fields: frozenset[str]
    state_fields: frozenset[str]

    @classmethod
    def from_spec(cls, spec: ToolSpec) -> ToolConfigContext:
        state_fields: Iterable[str] = (
            spec.state_model.model_fields if spec.state_model is not None else ()
        )
        return cls(
            command=spec.command,
            section=spec.section,
            profile_fields=frozenset(spec.profile_model.model_fields),
            state_fields=frozenset(state_fields),
        )

    def resolve_key(self, key: str) -> str:
        """Map a user key to the concrete config key.

        SDK roots win first: a tool field literally named ``log_level``,
        ``http``, or ``ui`` must not capture those SDK-owned settings.
        """
        first, rest = _split_first(key)
        if first in Settings.model_fields:
            return key
        if first in self.state_fields:
            raise self._state_error(key)
        if first == self.section and rest is not None:
            state_first, _ = _split_first(rest)
            if state_first in self.state_fields:
                raise self._state_error(key)
        if first in self.profile_fields:
            return f"{self.section}.{key}"
        return key

    def _state_error(self, key: str) -> ConfigError:
        return ConfigError(
            f"{key!r} is managed by {self.command} and is not a configurable setting"
        )


def _split_first(key: str) -> tuple[str, str | None]:
    first, sep, rest = key.partition(".")
    return first, rest if sep else None


__all__ = ["ToolConfigContext"]
