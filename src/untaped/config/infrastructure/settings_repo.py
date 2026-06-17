"""Adapter wiring schema introspection + YAML I/O + env detection together.

Scope (profile) awareness goes through the settings layout: writes land in
``profiles.<target>``. SDK-owned *global* sections (``ui`` and ``http``) are
addressed by a ``<section>.`` prefix and written at the top level instead of
within a profile.
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from untaped.config_file import (
    mutate_config,
    read_config_dict,
    set_at_path,
    unset_at_path,
)
from untaped.config_schema import FieldDescriptor, find_descriptor, walk_settings
from untaped.errors import ConfigError, first_validation_error
from untaped.settings import (
    BUILTIN_STATE_SECTIONS,
    Settings,
    active_settings_layout,
    get_profile_settings_model,
    get_settings,
    splice_registered_state,
    validate_settings_isolated,
)

GLOBAL_SETTINGS_TARGET = "global"
#: Sections addressed with a ``<section>.`` prefix and written at the top
#: level rather than within a profile: the SDK globals ``ui`` and ``http``.
GLOBAL_SECTIONS = ("ui", "http")


class SettingsFileRepository:
    """Single concrete adapter for everything ``config`` needs."""

    def __init__(self, settings_cls: type[Settings] | None = None) -> None:
        self._settings_cls = settings_cls
        self._descriptors: list[FieldDescriptor] | None = None
        self._global_descriptor_cache: dict[str, list[FieldDescriptor]] = {}

    def descriptors(self) -> list[FieldDescriptor]:
        if self._descriptors is None:
            self._descriptors = walk_settings(self._settings_cls or get_profile_settings_model())
        return self._descriptors

    def descriptor(self, key: str) -> FieldDescriptor:
        descriptors = self.descriptors()
        descriptor = find_descriptor(descriptors, key)
        if descriptor is None:
            valid = ", ".join(d.key for d in descriptors)
            raise ConfigError(f"unknown setting: {key!r}. Valid keys: {valid}")
        return descriptor

    # ----- global (top-level) sections -----

    def global_section_of(self, key: str) -> str | None:
        """Return the global section a key addresses (``ui.theme`` -> ``ui``), or None."""
        for section in GLOBAL_SECTIONS:
            if key.startswith(section + "."):
                return section
        return None

    def _global_model(self, section: str) -> type[BaseModel]:
        return BUILTIN_STATE_SECTIONS[section]

    def _global_descriptors(self, section: str) -> list[FieldDescriptor]:
        cached = self._global_descriptor_cache.get(section)
        if cached is None:
            cached = [
                replace(descriptor, path=(section, *descriptor.path))
                for descriptor in walk_settings(self._global_model(section))
            ]
            self._global_descriptor_cache[section] = cached
        return cached

    def global_descriptor(self, key: str, section: str) -> FieldDescriptor:
        descriptors = self._global_descriptors(section)
        descriptor = find_descriptor(descriptors, key)
        if descriptor is None:
            valid = ", ".join(d.key for d in descriptors)
            raise ConfigError(f"unknown {section} setting: {key!r}. Valid keys: {valid}")
        return descriptor

    def current_settings(self) -> Settings:
        return get_settings()

    def yaml_dict(self) -> dict[str, Any]:
        """The raw, unmerged YAML dict (top-level)."""
        return read_config_dict()

    def provenance(self) -> dict[tuple[str, ...], str]:
        """Map every leaf path that came from YAML to its supplying scope."""
        try:
            return active_settings_layout().provenance(self.yaml_dict())
        except ConfigError:
            return {}

    def profile_names(self) -> list[str]:
        return active_settings_layout().scope_names(self.yaml_dict())

    def profile_data(self, name: str) -> dict[str, Any] | None:
        return active_settings_layout().scope_data(self.yaml_dict(), name)

    def scope_value_for(self, descriptor: FieldDescriptor, profile: str) -> Any:
        """Raw value at ``descriptor.path`` in ``profile``'s effective view.

        Resolves through the layout's layering (e.g. ``profiles.default``
        beneath ``profiles.<profile>``); returns ``None`` when the scope's
        view doesn't set the leaf.
        """
        effective = active_settings_layout().effective(self.yaml_dict(), scope=profile)
        cursor: Any = effective
        for segment in descriptor.path:
            if not isinstance(cursor, dict) or segment not in cursor:
                return None
            cursor = cursor[segment]
        return cursor

    def env_var_for(self, descriptor: FieldDescriptor) -> str:
        return "UNTAPED_" + "__".join(descriptor.path).upper()

    def env_value_for(self, descriptor: FieldDescriptor) -> str | None:
        return os.environ.get(self.env_var_for(descriptor))

    def set_value(self, key: str, raw_value: str, *, profile: str | None = None) -> str:
        """Coerce ``raw_value``, validate against the schema, then persist.

        Returns the resolved target scope name (a profile name, or
        ``"global"`` for top-level global settings) so callers can report
        where the write landed.
        """
        section = self.global_section_of(key)
        if section is not None:
            return self._set_global_value(key, raw_value, section, profile=profile)
        descriptor = self.descriptor(key)
        coerced = _coerce_scalar(raw_value)
        resolved: str | None = None

        def _apply(data: dict[str, Any]) -> None:
            nonlocal resolved
            target_data, resolved = active_settings_layout().write_scope(data, profile)
            set_at_path(target_data, descriptor.path, coerced)
            merged = _merge_for_validation(data, scope=resolved)
            try:
                validate_settings_isolated(merged, self._settings_cls)
            except ValidationError as exc:
                raise ConfigError(
                    f"invalid value for {key!r}: {first_validation_error(exc)}"
                ) from exc

        mutate_config(_apply)
        # ``mutate_config`` always runs ``_apply``, which sets ``resolved`` from
        # ``write_scope`` (always a scope name).
        assert resolved is not None
        return resolved

    def _set_global_value(
        self, key: str, raw_value: str, section: str, *, profile: str | None
    ) -> str:
        if profile is not None:
            raise ConfigError(f"{section} settings are global; --target-profile cannot be used")
        descriptor = self.global_descriptor(key, section)
        coerced = _coerce_scalar(raw_value)
        model = self._global_model(section)

        def _apply(data: dict[str, Any]) -> None:
            set_at_path(data, descriptor.path, coerced)
            _validate_global_state(data, key, section, model)

        mutate_config(_apply)
        return GLOBAL_SETTINGS_TARGET

    def unset_value(self, key: str, *, profile: str | None = None) -> tuple[bool, str]:
        """Remove ``key`` from the resolved write scope.

        Returns ``(removed, target)``. An explicit ``--target-profile`` the
        layout cannot satisfy raises ``ConfigError``. Removing a key that
        simply isn't set in the resolved scope is a no-op
        (``removed=False``).
        """
        section = self.global_section_of(key)
        if section is not None:
            return self._unset_global_value(key, section, profile=profile)
        descriptor = self.descriptor(key)
        removed = False
        resolved: str | None = None

        def _apply(data: dict[str, Any]) -> None:
            nonlocal removed, resolved
            target_data, resolved = active_settings_layout().write_scope(data, profile)
            if not unset_at_path(target_data, descriptor.path):
                return
            removed = True
            # Symmetric with ``set_value``: re-merge and re-validate so a
            # removal that would leave the scope in a state pydantic would
            # reject surfaces here (with the offending key in the message),
            # not at next-load with an opaque traceback.
            merged = _merge_for_validation(data, scope=resolved)
            try:
                validate_settings_isolated(merged, self._settings_cls)
            except ValidationError as exc:
                raise ConfigError(
                    f"unsetting {key!r} would leave profile {resolved!r} invalid: "
                    f"{first_validation_error(exc)}"
                ) from exc

        mutate_config(_apply)
        # ``write_scope`` (run inside ``_apply``, before the early return) always
        # sets ``resolved`` to a scope name.
        assert resolved is not None
        return removed, resolved

    def _unset_global_value(
        self, key: str, section: str, *, profile: str | None
    ) -> tuple[bool, str]:
        if profile is not None:
            raise ConfigError(f"{section} settings are global; --target-profile cannot be used")
        descriptor = self.global_descriptor(key, section)
        removed = False
        model = self._global_model(section)

        def _apply(data: dict[str, Any]) -> None:
            nonlocal removed
            removed = unset_at_path(data, descriptor.path)
            if removed:
                _validate_global_state(data, key, section, model)

        mutate_config(_apply)
        return removed, GLOBAL_SETTINGS_TARGET


def _validate_global_state(
    data: dict[str, Any], key: str, section: str, model: type[BaseModel]
) -> None:
    raw = data.get(section) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"invalid value for {key!r}: Input should be a valid dictionary")
    try:
        model.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid value for {key!r}: {first_validation_error(exc)}") from exc


def _merge_for_validation(data: dict[str, Any], *, scope: str | None) -> dict[str, Any]:
    """Resolve the config as if ``scope`` were the live one.

    Used when writing to a scope that isn't the ambient active one — the
    schema check has to validate the target scope's view, otherwise the
    invalid value silently lands on disk and only fails when the scope is
    later activated.
    """
    effective = active_settings_layout().effective(data, scope=scope)
    splice_registered_state(data, effective)
    return effective


def _coerce_scalar(raw_value: str) -> Any:
    """Parse a CLI-supplied string as a YAML scalar.

    Handles ``"true"`` → ``True``, ``"42"`` → ``42``, ``"null"`` → ``None``,
    leaving non-scalar strings untouched. Pydantic does the final type
    coercion when we validate the merged dict.
    """
    return yaml.safe_load(raw_value)
