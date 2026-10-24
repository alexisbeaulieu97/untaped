"""The SDK's settings layout: map the raw config file to effective values.

`ProfilesSettingsLayout` layers the picked profiles (``profiles.<name>``)
over the base (``config.yml``'s top level) and is the SDK's only layout:
every plugin resolves through it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from untaped.runtime.profile_resolver import (
    DEFAULT_PROFILE,
    base_sections,
    check_defined,
    named_profiles,
    picked_names,
    profile_entries,
    resolve_profiles,
    write_target,
)
from untaped.sdk.errors import ConfigError
from untaped.sdk.health.deprecated_keys import KeyUse, rename_keys
from untaped.sdk.words import hint, not_found

SectionModels = Mapping[str, type[BaseModel]]


@dataclass(frozen=True)
class ResolvedConfig:
    """Effective values, their provenance, and the old keys each layer used."""

    effective: dict[str, Any]
    provenance: dict[tuple[str, ...], str]
    uses: dict[str, dict[str, tuple[KeyUse, ...]]]
    """``profile -> section -> uses``, for the layered profiles only."""


class ProfilesSettingsLayout:
    """Layer the picked ``profiles.<name>`` over the base, ``config.yml``'s top level, in order.

    Profiles are a core feature, not a plugin, so this layout (and the
    resolver it delegates to) lives in core.

    ``sections`` supplies the default section models: each layered profile's
    section data is rewritten to current key names (``renamed_keys``) before
    layering, so an old key in the base and the new key in the picked
    profile layer as one key. Without it, nothing is renamed.
    """

    def __init__(self, sections: Callable[[], SectionModels] | None = None) -> None:
        self._sections = sections

    def effective(self, raw: dict[str, Any], *, profile: str | None = None) -> dict[str, Any]:
        """Return the effective settings values for the run's pick (or the given profile)."""
        return self.resolve(raw, profile=profile).effective

    def inherited(self, raw: dict[str, Any], profile: str) -> dict[str, Any]:
        """Return the effective values ``profile`` inherits: the base's, nothing for the base."""
        if profile == DEFAULT_PROFILE:
            return {}
        return self.effective(raw, profile=DEFAULT_PROFILE)

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

        ``profile`` layers that one profile over the base instead of the
        run's pick. ``sections`` overrides the constructor's section models.
        """
        picks = (profile,) if profile else picked_names()
        models = sections if sections is not None else self._sections_now()
        renamed, uses = _rename_profiles(raw, models, picks)
        effective, provenance = resolve_profiles(renamed, picks=picks)
        return ResolvedConfig(effective, provenance, uses)

    def _sections_now(self) -> SectionModels:
        return self._sections() if self._sections is not None else {}

    def profile_names(self, raw: dict[str, Any]) -> list[str]:
        """Return ``default`` (the base), then the named profiles by name."""
        profiles = raw.get("profiles")
        return [DEFAULT_PROFILE, *(sorted(profiles) if isinstance(profiles, dict) else ())]

    def profile_data(self, raw: dict[str, Any], name: str) -> dict[str, Any] | None:
        """Return one profile's raw values (the base's for ``default``); ``None`` if undefined."""
        if name == DEFAULT_PROFILE:
            return base_sections(raw)
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
        """Return the mapping a write lands in: ``raw`` itself for the base.

        A named target must already exist: this is the guardrail that
        keeps ``untaped --profile typo config set`` from silently creating a
        new profile.
        """
        name = requested or write_target()
        known = named_profiles(raw)
        check_defined(picked_names(), known)  # a run whose pick is undefined writes nothing
        if name == DEFAULT_PROFILE:
            return raw, name
        if name not in known:
            raise ConfigError(
                f"{not_found('profile', name, known=sorted(known))}\n"
                f"{hint(f'profile create {name}')}"
            )
        target = known[name]
        if target is None:
            # An empty ``name:`` entry (YAML null): start it fresh.
            target = raw["profiles"][name] = {}
        return target, name


def _rename_profiles(
    raw: dict[str, Any], models: SectionModels, picks: Sequence[str]
) -> tuple[dict[str, Any], dict[str, dict[str, tuple[KeyUse, ...]]]]:
    """Copy of ``raw`` with the layered profiles' sections at current key names."""
    profiles = raw.get("profiles")
    if not models or not (profiles is None or isinstance(profiles, dict)):
        return raw, {}  # ``resolve_profiles`` reports a malformed ``profiles``
    layered = {DEFAULT_PROFILE, *picks}
    renamed = {**raw, "profiles": dict(profiles or {})}
    uses: dict[str, dict[str, tuple[KeyUse, ...]]] = {}
    for name, data, path in profile_entries(raw):
        if name not in layered:
            continue
        sections: dict[str, Any] = {}
        for section, model in models.items():
            section_data = data.get(section)
            if not isinstance(section_data, dict):
                continue
            sections[section], section_uses = rename_keys(model, section_data)
            if section_uses:
                uses.setdefault(name, {})[section] = section_uses
        if path:
            renamed["profiles"][name] = {**data, **sections}
        else:
            renamed.update(sections)
    if "profiles" not in raw:
        del renamed["profiles"]
    return renamed, uses
