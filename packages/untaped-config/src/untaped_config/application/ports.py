"""Application-layer protocols (ports) for the config bounded context."""

from __future__ import annotations

from typing import Any, Protocol

from untaped_core import FieldDescriptor, Settings


class SettingsReader(Protocol):
    """Read-side surface for ``ListSettings`` / ``ListAllProfilesSettings``.

    The seven methods here power the ``untaped config list`` table —
    descriptors + effective values + sources + per-profile leaf values.
    Mutating use cases take the wider :class:`SettingsRepository` below,
    which adds ``set_value`` / ``unset_value``.
    """

    def descriptors(self) -> list[FieldDescriptor]: ...
    def current_settings(self) -> Settings: ...
    def yaml_dict(self) -> dict[str, Any]: ...
    def env_value_for(self, descriptor: FieldDescriptor) -> str | None: ...
    def provenance(self) -> dict[tuple[str, ...], str]: ...
    def profile_data(self, name: str) -> dict[str, Any] | None: ...
    def profile_names(self) -> list[str]: ...


class SettingsRepository(SettingsReader, Protocol):
    """Read + write surface used by ``SetSetting`` / ``UnsetSetting``.

    The single concrete adapter (``SettingsFileRepository``) implements
    every method structurally; per-command use cases call only the
    subset they need.
    """

    def set_value(self, key: str, raw_value: str, *, profile: str | None = None) -> str: ...
    def unset_value(self, key: str, *, profile: str | None = None) -> tuple[bool, str]: ...
