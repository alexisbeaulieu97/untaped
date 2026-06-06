"""Adapter wiring schema introspection + YAML I/O + env detection together."""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any, Literal

import yaml
from pydantic import ValidationError

from untaped import (
    DEFAULT_PROFILE,
    ConfigError,
    FieldDescriptor,
    Settings,
    UiSettings,
    effective_active_profile_name,
    find_descriptor,
    first_validation_error,
    get_profile_settings_model,
    get_settings,
    resolve_profiles,
    splice_registered_state,
    validate_settings_isolated,
    walk_settings,
)
from untaped.config_file import (
    list_profile_names,
    mutate_config,
    read_config_dict,
    set_at_path,
    unset_at_path,
)

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
        """Map every leaf path that came from YAML to its profile name."""
        data = self.yaml_dict()
        try:
            _, prov = resolve_profiles(data, active_override=effective_active_profile_name(data))
        except ConfigError:
            return {}
        return prov

    def profile_names(self) -> list[str]:
        return list_profile_names()

    def profile_data(self, name: str) -> dict[str, Any] | None:
        profiles = self.yaml_dict().get("profiles") or {}
        if not isinstance(profiles, dict):
            return None
        profile = profiles.get(name)
        return profile if isinstance(profile, dict) else None

    def env_var_for(self, descriptor: FieldDescriptor) -> str:
        return "UNTAPED_" + "__".join(descriptor.path).upper()

    def env_value_for(self, descriptor: FieldDescriptor) -> str | None:
        return os.environ.get(self.env_var_for(descriptor))

    def set_value(self, key: str, raw_value: str, *, profile: str | None = None) -> str:
        """Coerce ``raw_value``, validate against the schema, then persist.

        Returns the resolved target profile name, or ``"global"`` for
        top-level UI settings, so callers can report where the write landed.
        """
        if _is_global_ui_key(key):
            return self._set_ui_value(key, raw_value, profile=profile)
        descriptor = self.descriptor(key)
        coerced = _coerce_scalar(raw_value)
        resolved: str = ""

        def _apply(data: dict[str, Any]) -> None:
            nonlocal resolved
            target = self._resolve_target_profile(data, profile)
            resolved = target
            profiles = _ensure_profiles_dict(data)
            profile_data = profiles.setdefault(target, {})
            if not isinstance(profile_data, dict):
                profile_data = {}
                profiles[target] = profile_data
            set_at_path(profile_data, descriptor.path, coerced)
            merged = _merge_for_validation(data, active=target)
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

    def unset_value(self, key: str, *, profile: str | None = None) -> tuple[bool, str]:
        """Remove ``key`` from the resolved profile.

        Returns ``(removed, target)``. An explicit ``--target-profile`` that names a
        profile which doesn't exist raises ``ConfigError``. Removing a key
        that simply isn't set in the resolved profile is a no-op
        (``removed=False``).
        """
        if _is_global_ui_key(key):
            return self._unset_ui_value(key, profile=profile)
        descriptor = self.descriptor(key)
        removed = False
        resolved: str = ""

        def _apply(data: dict[str, Any]) -> None:
            nonlocal removed, resolved
            target = self._resolve_target_profile(data, profile)
            resolved = target
            profiles = data.get("profiles")
            if not isinstance(profiles, dict):
                return
            profile_data = profiles.get(target)
            if not isinstance(profile_data, dict):
                return
            if not unset_at_path(profile_data, descriptor.path):
                return
            removed = True
            # Symmetric with ``set_value``: re-merge and re-validate so a
            # removal that would leave the profile in a state pydantic
            # would reject surfaces here (with the offending key in the
            # message), not at next-load with an opaque traceback. Almost
            # every field today has a schema default that fills the gap,
            # so this is preventive plumbing for future required-without-
            # default fields.
            merged = _merge_for_validation(data, active=target)
            try:
                validate_settings_isolated(merged, self._settings_cls)
            except ValidationError as exc:
                raise ConfigError(
                    f"unsetting {key!r} would leave profile {target!r} invalid: "
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

    def _resolve_target_profile(self, data: dict[str, Any], profile: str | None) -> str:
        """Resolve the target profile for a ``set`` or ``unset``, validating
        that the resolved profile exists.

        ``default`` is exempt from the check — it's the auto-created floor
        when nothing else is named (no explicit target, no ``active:``, no
        ``UNTAPED_PROFILE``).
        """
        if profile is not None:
            if profile == DEFAULT_PROFILE:
                return profile
            return self._require_existing(data, profile, source="explicit")
        recorded = effective_active_profile_name(data)
        if not recorded or recorded == DEFAULT_PROFILE:
            return DEFAULT_PROFILE
        return self._require_existing(data, recorded, source="active")

    def _require_existing(
        self,
        data: dict[str, Any],
        name: str,
        *,
        source: Literal["explicit", "active"],
    ) -> str:
        existing = data.get("profiles") or {}
        if isinstance(existing, dict) and name in existing:
            return name
        known = ", ".join(sorted(existing)) if isinstance(existing, dict) else ""
        known_str = known or "(none)"
        if source == "active":
            raise ConfigError(
                f"active profile {name!r} does not exist; "
                f"known profiles: {known_str}. "
                "Install the `untaped-profile` plugin, then run "
                "`untaped profile use <name>` or `untaped profile create` first."
            )
        raise ConfigError(
            f"profile {name!r} does not exist; "
            f"known profiles: {known_str}. "
            "Install the `untaped-profile` plugin, then create it first with "
            "`untaped profile create`."
        )


def _ensure_profiles_dict(data: dict[str, Any]) -> dict[str, Any]:
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        profiles = {}
        data["profiles"] = profiles
    return profiles


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


def _merge_for_validation(data: dict[str, Any], *, active: str) -> dict[str, Any]:
    """Run the resolver as if ``active`` were the live profile.

    Used when writing to a profile that isn't the ambient active one — the
    schema check has to validate the target profile's view, otherwise the
    invalid value silently lands on disk and only fails when the profile
    is later activated.
    """
    effective, _ = resolve_profiles(data, active_override=active)
    splice_registered_state(data, effective)
    return effective


def _coerce_scalar(raw_value: str) -> Any:
    """Parse a CLI-supplied string as a YAML scalar.

    Handles ``"true"`` → ``True``, ``"42"`` → ``42``, ``"null"`` → ``None``,
    leaving non-scalar strings untouched. Pydantic does the final type
    coercion when we validate the merged dict.
    """
    return yaml.safe_load(raw_value)
