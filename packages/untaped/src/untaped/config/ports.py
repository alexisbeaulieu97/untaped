"""Protocols (ports) for the root config command group."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from pydantic import BaseModel

from untaped.config.models import OldSpelling
from untaped.config_schema import FieldDescriptor
from untaped.stability import Stability


class SettingsReader(Protocol):
    """Read-side surface used by config listing, get, and prompt-default flows."""

    def descriptor(self, key: str) -> FieldDescriptor: ...
    def descriptors(self) -> list[FieldDescriptor]: ...
    def setting_value(self, descriptor: FieldDescriptor) -> Any: ...
    def holder_value(self, descriptor: FieldDescriptor) -> Any: ...
    def raw_setting_value(self, descriptor: FieldDescriptor) -> Any: ...
    def env_var_for(self, descriptor: FieldDescriptor) -> str: ...
    def env_var_supplying(self, descriptor: FieldDescriptor) -> str | None: ...
    def env_value_for(self, descriptor: FieldDescriptor) -> str | None: ...
    def deprecated_source(self, descriptor: FieldDescriptor) -> OldSpelling | None: ...
    def mark(self, descriptor: FieldDescriptor) -> Stability | None: ...
    def section_model(self, section: str) -> type[BaseModel] | None: ...
    def provenance(self) -> dict[tuple[str, ...], str]: ...
    def profile_data(self, name: str) -> dict[str, Any] | None: ...
    def profile_names(self) -> list[str]: ...


class SettingsWriter(Protocol):
    """Write-side surface the config use cases change settings through."""

    def own_value(self, key: str, *, profile: str | None = None) -> Any: ...

    def set_value(
        self,
        key: str,
        raw_value: str,
        *,
        profile: str | None = None,
        dry_run: bool = False,
        on_spelling_removed: Callable[[str], None] | None = None,
    ) -> str: ...
    def update_value(
        self,
        key: str,
        update: Callable[[str, Any], str | None],
        *,
        profile: str | None = None,
        dry_run: bool = False,
        on_spelling_removed: Callable[[str], None] | None = None,
    ) -> str: ...
    def unset_value(
        self,
        key: str,
        *,
        profile: str | None = None,
        dry_run: bool = False,
        on_spelling_removed: Callable[[str], None] | None = None,
    ) -> tuple[bool, str]: ...


__all__ = ["SettingsReader", "SettingsWriter"]
