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
    list_profile_names,
    read_config_dict,
    set_at_path,
    unset_at_path,
    write_config_dict,
)
from untaped_core.config_schema import FieldDescriptor, find_descriptor, walk_settings
from untaped_core.profile_resolver import resolve_profiles


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
        """The raw, unmerged YAML dict (top-level)."""
        return read_config_dict()

    def provenance(self) -> dict[tuple[str, ...], str]:
        """Map every leaf path that came from YAML to its profile name."""
        active_override = os.environ.get("UNTAPED_PROFILE") or None
        try:
            _, prov = resolve_profiles(self.yaml_dict(), active_override=active_override)
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

    def set_value(self, key: str, raw_value: str, *, profile: str | None = None) -> None:
        """Coerce ``raw_value``, validate against the schema, then persist."""
        descriptor = self.descriptor(key)
        coerced = _coerce_scalar(raw_value)
        data = self.yaml_dict()
        target = self._resolve_target_profile(data, profile)
        profiles = _ensure_profiles_dict(data)
        profile_data = profiles.setdefault(target, {})
        if not isinstance(profile_data, dict):
            profile_data = {}
            profiles[target] = profile_data
        set_at_path(profile_data, descriptor.path, coerced)
        merged = _merge_for_validation(data)
        try:
            self._settings_cls.model_validate(merged)
        except ValidationError as exc:
            raise ConfigError(f"invalid value for {key!r}: {first_validation_error(exc)}") from exc
        write_config_dict(data)
        get_settings.cache_clear()

    def unset_value(self, key: str, *, profile: str | None = None) -> bool:
        descriptor = self.descriptor(key)
        data = self.yaml_dict()
        profiles = data.get("profiles")
        if not isinstance(profiles, dict):
            return False
        target = profile or _current_active_profile_name(data)
        if target not in profiles or not isinstance(profiles[target], dict):
            return False
        profile_data = profiles[target]
        if get_at_path(profile_data, descriptor.path) is MISSING:
            return False
        unset_at_path(profile_data, descriptor.path)
        write_config_dict(data)
        get_settings.cache_clear()
        return True

    def _resolve_target_profile(self, data: dict[str, Any], profile: str | None) -> str:
        """Decide which profile a ``set`` writes to, validating existence
        when an explicit profile was named."""
        if profile is None:
            return _current_active_profile_name(data)
        if profile == "default":
            return profile
        existing = data.get("profiles") or {}
        if not isinstance(existing, dict) or profile not in existing:
            known = sorted(existing) if isinstance(existing, dict) else []
            raise ConfigError(
                f"profile {profile!r} does not exist; "
                f"known profiles: {', '.join(known) or '(none)'}. "
                "Create it first with `untaped profile create`."
            )
        return profile


def _ensure_profiles_dict(data: dict[str, Any]) -> dict[str, Any]:
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        profiles = {}
        data["profiles"] = profiles
    return profiles


def _current_active_profile_name(data: dict[str, Any]) -> str:
    """Return the active profile recorded in ``data`` (env override applied)."""
    name = os.environ.get("UNTAPED_PROFILE") or data.get("active") or "default"
    if not isinstance(name, str) or not name:
        return "default"
    return name


def _merge_for_validation(data: dict[str, Any]) -> dict[str, Any]:
    """Run the resolver to get a merged dict suitable for Pydantic validation."""
    active_override = os.environ.get("UNTAPED_PROFILE") or None
    effective, _ = resolve_profiles(data, active_override=active_override)
    # Splice the workspace registry like ProfilesSettingsSource does so the
    # full Settings model can validate.
    ws_state = data.get("workspace")
    if isinstance(ws_state, dict) and "workspaces" in ws_state:
        merged_ws = effective.setdefault("workspace", {})
        if isinstance(merged_ws, dict):
            merged_ws["workspaces"] = ws_state["workspaces"]
    return effective


def _coerce_scalar(raw_value: str) -> Any:
    """Parse a CLI-supplied string as a YAML scalar.

    Handles ``"true"`` → ``True``, ``"42"`` → ``42``, ``"null"`` → ``None``,
    leaving non-scalar strings untouched. Pydantic does the final type
    coercion when we validate the merged dict.
    """
    return yaml.safe_load(raw_value)
