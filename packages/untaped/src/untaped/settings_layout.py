"""The SDK's settings layout: map the raw config file to effective values.

`ProfilesSettingsLayout` layers ``profiles.default`` beneath
``profiles.<active>`` and is the SDK's only layout — every tool resolves
through it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from untaped.deprecated_keys import KeyUse, rename_keys
from untaped.errors import ConfigError
from untaped.messages import hint, not_found
from untaped.profile_resolver import (
    DEFAULT_PROFILE,
    effective_active_profile_name,
    resolve_profiles,
)

SectionModels = Mapping[str, type[BaseModel]]


@dataclass(frozen=True)
class ResolvedConfig:
    """Effective values, their provenance, and the old keys each layer used."""

    effective: dict[str, Any]
    provenance: dict[tuple[str, ...], str]
    uses: dict[str, dict[str, tuple[KeyUse, ...]]]
    """``profile -> section -> uses``, for the layered profiles only."""


class ProfilesSettingsLayout:
    """Layer ``profiles.default`` beneath ``profiles.<active>``.

    Profiles are a first-class SDK capability, so this layout (and the
    resolver it delegates to) lives in core.

    ``sections`` supplies the default section models: each layered profile's
    section data is rewritten to current key names (``renamed_keys``) before
    layering, so an old key in ``default`` and the new key in the active
    profile layer as one key. Without it, nothing is renamed.
    """

    def __init__(self, sections: Callable[[], SectionModels] | None = None) -> None:
        self._sections = sections

    def effective(self, raw: dict[str, Any], *, profile: str | None = None) -> dict[str, Any]:
        """Return the effective settings values for the active (or given) profile."""
        return self.resolve(raw, profile=profile).effective

    def provenance(self, raw: dict[str, Any]) -> dict[tuple[str, ...], str]:
        """Return ``leaf path -> profile name`` for every value in ``effective``."""
        return self.resolve(raw).provenance

    def resolve(
        self,
        raw: dict[str, Any],
        *,
        profile: str | None = None,
        sections: SectionModels | None = None,
    ) -> ResolvedConfig:
        """Resolve effective settings, provenance and old-key uses.

        ``sections`` overrides the constructor's section models.
        """
        override = profile or effective_active_profile_name(raw)
        models = sections if sections is not None else self._sections_now()
        renamed, uses = _rename_profiles(raw, models, override)
        effective, provenance = resolve_profiles(renamed, active_override=override)
        return ResolvedConfig(effective, provenance, uses)

    def _sections_now(self) -> SectionModels:
        return self._sections() if self._sections is not None else {}

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
        keeps ``untaped --profile typo config set`` from silently creating a
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


def _rename_profiles(
    raw: dict[str, Any], models: SectionModels, active: str | None
) -> tuple[dict[str, Any], dict[str, dict[str, tuple[KeyUse, ...]]]]:
    """Copy of ``raw`` with the layered profiles' sections at current key names."""
    profiles = raw.get("profiles")
    if not models or not isinstance(profiles, dict):
        return raw, {}
    layered = [
        name
        for name in dict.fromkeys((DEFAULT_PROFILE, active or DEFAULT_PROFILE))
        if name in profiles
    ]
    renamed_profiles = dict(profiles)
    uses: dict[str, dict[str, tuple[KeyUse, ...]]] = {}
    for name in layered:
        data = profiles[name]
        if not isinstance(data, dict):
            continue  # ``resolve_profiles`` reports a malformed profile
        copy = dict(data)
        for section, model in models.items():
            section_data = data.get(section)
            if not isinstance(section_data, dict):
                continue
            copy[section], section_uses = rename_keys(model, section_data)
            if section_uses:
                uses.setdefault(name, {})[section] = section_uses
        renamed_profiles[name] = copy
    return {**raw, "profiles": renamed_profiles}, uses
