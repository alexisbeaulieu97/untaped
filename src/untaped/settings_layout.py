"""The SDK's settings layout: map the raw config file to effective values.

`ProfilesSettingsLayout` layers ``profiles.default`` beneath
``profiles.<active>`` and is the SDK's only layout — every tool resolves
through it.
"""

from __future__ import annotations

from typing import Any

from untaped.errors import ConfigError
from untaped.messages import hint, not_found
from untaped.profile_resolver import (
    DEFAULT_PROFILE,
    effective_active_profile_name,
    resolve_profiles,
)


class ProfilesSettingsLayout:
    """Layer ``profiles.default`` beneath ``profiles.<active>``.

    Profiles are a first-class SDK capability, so this layout (and the
    resolver it delegates to) lives in core.
    """

    def effective(self, raw: dict[str, Any], *, profile: str | None = None) -> dict[str, Any]:
        """Return the effective settings values for the active (or given) profile."""
        effective, _ = self._resolve(raw, profile)
        return effective

    def provenance(self, raw: dict[str, Any]) -> dict[tuple[str, ...], str]:
        """Return ``leaf path -> profile name`` for every value in ``effective``."""
        _, provenance = self._resolve(raw)
        return provenance

    def _resolve(
        self, raw: dict[str, Any], profile: str | None = None
    ) -> tuple[dict[str, Any], dict[tuple[str, ...], str]]:
        """Resolve effective settings + provenance for the active (or given) profile."""
        override = profile or effective_active_profile_name(raw)
        return resolve_profiles(raw, active_override=override)

    def profile_names(self, raw: dict[str, Any]) -> list[str]:
        """Return the selectable profile names."""
        profiles = raw.get("profiles")
        return sorted(profiles) if isinstance(profiles, dict) else []

    def profile_data(self, raw: dict[str, Any], name: str) -> dict[str, Any] | None:
        """Return one profile's raw values, or ``None`` when undefined."""
        profiles = raw.get("profiles")
        if not isinstance(profiles, dict):
            return None
        if name in profiles and profiles[name] is None:
            return {}
        data = profiles.get(name)
        return data if isinstance(data, dict) else None

    def write_profile(
        self, raw: dict[str, Any], requested: str | None
    ) -> tuple[dict[str, Any], str]:
        """Return the target profile's dict, creating only ``default``.

        Any other target must already exist — this is the guardrail that
        keeps ``config set --target-profile typo`` from silently creating a
        new profile.
        """
        name = requested or effective_active_profile_name(raw) or DEFAULT_PROFILE
        existing = raw.get("profiles")
        known = existing if isinstance(existing, dict) else {}
        if name != DEFAULT_PROFILE and name not in known:
            raise ConfigError(
                f"{not_found('profile', name, known=sorted(known))}\n"
                f"{hint(f'profile create {name}')}"
            )
        profiles = raw.setdefault("profiles", {})
        if not isinstance(profiles, dict):
            raise ConfigError("config key 'profiles' must be a mapping")
        target = profiles.get(name)
        if target is None:
            # Absent, or an empty ``name:`` entry (YAML null): start it fresh.
            target = profiles[name] = {}
        if not isinstance(target, dict):
            raise ConfigError(f"profile {name!r} must be a mapping")
        return target, name
