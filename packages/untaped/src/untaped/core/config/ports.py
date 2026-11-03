"""Protocols (ports) for the root config command group."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from untaped.core.config.models import OldSpelling, Typed
from untaped.sdk._schema import FieldDescriptor
from untaped.sdk.marks import Stability

type Update = Callable[[str, Any], str | Typed | None]
"""A key's change in a write: given the target and the key's own value there, its new value.

The text ``config set`` takes, a :class:`Typed` value, or ``None`` to remove the key.
"""


class SettingsReader(Protocol):
    """Read-side surface used by config listing, get, and prompt-default flows."""

    def descriptor(self, key: str) -> FieldDescriptor: ...
    def descriptors(self) -> list[FieldDescriptor]: ...
    def setting_value(self, descriptor: FieldDescriptor) -> Any: ...
    def holder_value(self, descriptor: FieldDescriptor) -> Any: ...
    def validated_with(self, descriptor: FieldDescriptor) -> str: ...
    def raw_setting_value(self, descriptor: FieldDescriptor) -> Any: ...
    def env_var_for(self, descriptor: FieldDescriptor) -> str: ...
    def env_var_supplying(self, descriptor: FieldDescriptor) -> str | None: ...
    def env_var_setting(self, path: tuple[str, ...]) -> str | None: ...
    def inherited_value(self, descriptor: FieldDescriptor, profile: str) -> Any: ...
    def deprecated_source(self, descriptor: FieldDescriptor) -> OldSpelling | None: ...
    def mark(self, descriptor: FieldDescriptor) -> Stability | None: ...
    def section_model(self, section: str) -> type[BaseModel] | None: ...
    def provenance(self) -> dict[tuple[str, ...], str]: ...
    def profile_data(self, name: str) -> dict[str, Any] | None: ...
    def profile_names(self) -> list[str]: ...


class SettingsWriter(Protocol):
    """Write-side surface the config use cases change settings through."""

    def descriptor(self, key: str) -> FieldDescriptor: ...
    def provenance(self) -> dict[tuple[str, ...], str]: ...
    def own_value(self, key: str, *, profile: str | None = None) -> Any: ...
    def written_file(self) -> Path: ...
    def typed(self, key: str, value: object) -> Any: ...

    def update_value(
        self,
        key: str,
        update: Update,
        *,
        profile: str | None = None,
        dry_run: bool = False,
        on_spelling_removed: Callable[[str], None] | None = None,
    ) -> str: ...
    def update_values(
        self,
        updates: Mapping[str, Update],
        *,
        profile: str | None = None,
        dry_run: bool = False,
        on_spelling_removed: Callable[[str], None] | None = None,
        on_unchanged: Callable[[str], None] | None = None,
        home_only_to_base: bool = False,
    ) -> str: ...
    def unset_value(
        self,
        key: str,
        *,
        profile: str | None = None,
        dry_run: bool = False,
        on_spelling_removed: Callable[[str], None] | None = None,
    ) -> tuple[bool, str]: ...


__all__ = ["SettingsReader", "SettingsWriter", "Update"]
