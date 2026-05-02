"""Adapter wiring schema introspection + YAML I/O + env detection together."""

from __future__ import annotations

import os
from typing import Any

import yaml
from pydantic import ValidationError
from untaped_core import ConfigError, Settings, first_validation_error, get_settings
from untaped_core.config_file import (
    list_profile_names,
    mutate_config,
    read_config_dict,
    set_at_path,
    unset_at_path,
)
from untaped_core.config_schema import FieldDescriptor, find_descriptor, walk_settings
from untaped_core.profile_resolver import (
    DEFAULT_PROFILE,
    effective_active_profile_name,
    resolve_profiles,
    splice_workspace_registry,
)


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

        Returns the resolved target profile name so callers can report it.
        """
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
                self._settings_cls.model_validate(merged)
            except ValidationError as exc:
                raise ConfigError(
                    f"invalid value for {key!r}: {first_validation_error(exc)}"
                ) from exc

        mutate_config(_apply)
        return resolved

    def unset_value(self, key: str, *, profile: str | None = None) -> tuple[bool, str]:
        """Remove ``key`` from the resolved profile.

        Returns ``(removed, target)``. An explicit ``--profile`` that names a
        profile which doesn't exist raises ``ConfigError`` — same contract as
        ``set_value`` — so a typo can't masquerade as a successful no-op.
        Removing a key that simply isn't set in the resolved profile is still
        a clean no-op (``removed=False``).
        """
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

        mutate_config(_apply)
        return removed, resolved

    def _resolve_target_profile(self, data: dict[str, Any], profile: str | None) -> str:
        """Decide which profile a ``set`` writes to, validating existence
        when an explicit profile was named."""
        if profile is None:
            return _current_active_profile_name(data)
        if profile == DEFAULT_PROFILE:
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
    return effective_active_profile_name(data) or DEFAULT_PROFILE


def _merge_for_validation(data: dict[str, Any], *, active: str) -> dict[str, Any]:
    """Run the resolver as if ``active`` were the live profile.

    Used when writing to a profile that isn't the ambient active one — the
    schema check has to validate the target profile's view, otherwise the
    invalid value silently lands on disk and only fails when the profile
    is later activated.
    """
    effective, _ = resolve_profiles(data, active_override=active)
    splice_workspace_registry(data, effective)
    return effective


def _coerce_scalar(raw_value: str) -> Any:
    """Parse a CLI-supplied string as a YAML scalar.

    Handles ``"true"`` → ``True``, ``"42"`` → ``42``, ``"null"`` → ``None``,
    leaving non-scalar strings untouched. Pydantic does the final type
    coercion when we validate the merged dict.
    """
    return yaml.safe_load(raw_value)
