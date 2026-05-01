"""Adapter wiring schema introspection + YAML I/O + env detection together."""

from __future__ import annotations

import os
from typing import Any

import yaml
from pydantic import ValidationError
from untaped_core import ConfigError, Settings, first_validation_error, get_settings
from untaped_core.config_file import (
    MISSING,
    get_at_path,
    read_config_dict,
    set_at_path,
    unset_at_path,
    write_config_dict,
)
from untaped_core.config_schema import FieldDescriptor, find_descriptor, walk_settings


class SettingsFileRepository:
    """Single concrete adapter for everything ``untaped config`` needs."""

    def __init__(self, settings_cls: type[Settings] = Settings) -> None:
        self._settings_cls = settings_cls
        self._descriptors: list[FieldDescriptor] | None = None

    def descriptors(self) -> list[FieldDescriptor]:
        if self._descriptors is None:
            self._descriptors = walk_settings(self._settings_cls)
        return self._descriptors

    def descriptor(self, key: str) -> FieldDescriptor:
        descriptors = self.descriptors()
        descriptor = find_descriptor(descriptors, key)
        if descriptor is None:
            valid = ", ".join(d.key for d in descriptors)
            raise ConfigError(f"unknown setting: {key!r}. Valid keys: {valid}")
        return descriptor

    def current_settings(self) -> Settings:
        return get_settings()

    def yaml_dict(self) -> dict[str, Any]:
        return read_config_dict()

    def env_var_for(self, descriptor: FieldDescriptor) -> str:
        return "UNTAPED_" + "__".join(descriptor.path).upper()

    def env_value_for(self, descriptor: FieldDescriptor) -> str | None:
        return os.environ.get(self.env_var_for(descriptor))

    def set_value(self, key: str, raw_value: str) -> None:
        """Coerce ``raw_value``, validate against the schema, then persist."""
        descriptor = self.descriptor(key)
        coerced = _coerce_scalar(raw_value)
        data = self.yaml_dict()
        set_at_path(data, descriptor.path, coerced)
        try:
            self._settings_cls.model_validate(data)
        except ValidationError as exc:
            raise ConfigError(f"invalid value for {key!r}: {first_validation_error(exc)}") from exc
        write_config_dict(data)
        get_settings.cache_clear()

    def unset_value(self, key: str) -> bool:
        descriptor = self.descriptor(key)
        data = self.yaml_dict()
        if get_at_path(data, descriptor.path) is MISSING:
            return False
        unset_at_path(data, descriptor.path)
        write_config_dict(data)
        get_settings.cache_clear()
        return True


def _coerce_scalar(raw_value: str) -> Any:
    """Parse a CLI-supplied string as a YAML scalar.

    Handles ``"true"`` → ``True``, ``"42"`` → ``42``, ``"null"`` → ``None``,
    leaving non-scalar strings untouched. Pydantic does the final type
    coercion when we validate the merged dict.
    """
    return yaml.safe_load(raw_value)
