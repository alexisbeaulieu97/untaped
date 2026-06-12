"""Adapter wiring schema introspection + YAML I/O + env detection together.

All scope (profile) awareness goes through the registered settings layout:
with the flat default, writes land on top-level keys; with the
untaped-profile plugin's layout, writes land in ``profiles.<target>``.
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any

import yaml
from pydantic import ValidationError

from untaped import (
    ConfigError,
    FieldDescriptor,
    Settings,
    UiSettings,
    find_descriptor,
    first_validation_error,
    get_profile_settings_model,
    get_settings,
    splice_registered_state,
    validate_settings_isolated,
    walk_settings,
)
from untaped.config_file import (
    mutate_config,
    read_config_dict,
    set_at_path,
    unset_at_path,
)
from untaped.settings import active_settings_layout

GLOBAL_SETTINGS_TARGET = "global"
_GLOBAL_UI_PREFIX = "ui."


class SettingsFileRepository:
    """Single concrete adapter for everything ``untaped config`` needs."""

    def __init__(self, settings_cls: type[Settings] | None = None) -> None:
        self._settings_cls = settings_cls
        self._descriptors: list[FieldDescriptor] | None = None
        self._ui_descriptors: list[FieldDescriptor] | None = None

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

    def ui_descriptors(self) -> list[FieldDescriptor]:
        if self._ui_descriptors is None:
            self._ui_descriptors = [
                replace(descriptor, path=("ui", *descriptor.path))
                for descriptor in walk_settings(UiSettings)
            ]
        return self._ui_descriptors

    def ui_descriptor(self, key: str) -> FieldDescriptor:
        descriptors = self.ui_descriptors()
        descriptor = find_descriptor(descriptors, key)
        if descriptor is None:
            valid = ", ".join(d.key for d in descriptors)
            raise ConfigError(f"unknown UI setting: {key!r}. Valid keys: {valid}")
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

    def supports_profiles(self) -> bool:
        """Whether the active settings layout has selectable scopes."""
        return active_settings_layout().supports_scopes

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

    def set_value(self, key: str, raw_value: str, *, profile: str | None = None) -> str | None:
        """Coerce ``raw_value``, validate against the schema, then persist.

        Returns the resolved target scope name (``None`` for scope-less
        layouts, ``"global"`` for top-level UI settings) so callers can
        report where the write landed.
        """
        if _is_global_ui_key(key):
            return self._set_ui_value(key, raw_value, profile=profile)
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
        return resolved

    def _set_ui_value(self, key: str, raw_value: str, *, profile: str | None) -> str:
        if profile is not None:
            raise ConfigError("ui settings are global; --target-profile cannot be used")
        descriptor = self.ui_descriptor(key)
        coerced = _coerce_scalar(raw_value)

        def _apply(data: dict[str, Any]) -> None:
            set_at_path(data, descriptor.path, coerced)
            _validate_ui_state(data, key)

        mutate_config(_apply)
        return GLOBAL_SETTINGS_TARGET

    def unset_value(self, key: str, *, profile: str | None = None) -> tuple[bool, str | None]:
        """Remove ``key`` from the resolved write scope.

        Returns ``(removed, target)``. An explicit ``--target-profile`` the
        layout cannot satisfy raises ``ConfigError``. Removing a key that
        simply isn't set in the resolved scope is a no-op
        (``removed=False``).
        """
        if _is_global_ui_key(key):
            return self._unset_ui_value(key, profile=profile)
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
                    f"unsetting {key!r} would leave {_scope_label(resolved)} invalid: "
                    f"{first_validation_error(exc)}"
                ) from exc

        mutate_config(_apply)
        return removed, resolved

    def _unset_ui_value(self, key: str, *, profile: str | None) -> tuple[bool, str]:
        if profile is not None:
            raise ConfigError("ui settings are global; --target-profile cannot be used")
        descriptor = self.ui_descriptor(key)
        removed = False

        def _apply(data: dict[str, Any]) -> None:
            nonlocal removed
            removed = unset_at_path(data, descriptor.path)
            if removed:
                _validate_ui_state(data, key)

        mutate_config(_apply)
        return removed, GLOBAL_SETTINGS_TARGET


def _scope_label(scope: str | None) -> str:
    return f"profile {scope!r}" if scope else "the config"


def _is_global_ui_key(key: str) -> bool:
    return key.startswith(_GLOBAL_UI_PREFIX)


def _validate_ui_state(data: dict[str, Any], key: str) -> None:
    raw = data.get("ui") or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"invalid value for {key!r}: Input should be a valid dictionary")
    try:
        UiSettings.model_validate(raw)
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
