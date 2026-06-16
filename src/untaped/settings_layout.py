"""Layout protocol mapping the raw config file to effective settings.

A *settings layout* decides how the parsed ``~/.untaped/config.yml`` dict
becomes the effective settings values. The SDK ships one layout,
:class:`ProfilesSettingsLayout`, which layers ``profiles.default`` beneath
``profiles.<active>``. At most one layout is active, registered via
:func:`untaped.settings.register_settings_layout`; it is also the registry
default when no tool has registered one.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from untaped.errors import ConfigError
from untaped.identity import current_tool_command
from untaped.profile_resolver import (
    DEFAULT_PROFILE,
    effective_active_profile_name,
    resolve_profiles,
)


@runtime_checkable
class SettingsLayout(Protocol):
    """Maps the parsed config dict to effective settings values."""

    supports_scopes: bool

    def effective(self, raw: dict[str, Any], *, scope: str | None = None) -> dict[str, Any]:
        """Return the effective settings values.

        ``scope`` resolves the view as if that scope (profile) were active —
        used to validate writes that target a non-active scope. Layouts
        without scopes ignore it.
        """
        ...

    def provenance(self, raw: dict[str, Any]) -> dict[tuple[str, ...], str]:
        """Return ``leaf path -> scope name`` for every value in ``effective``."""
        ...

    def scope_names(self, raw: dict[str, Any]) -> list[str]:
        """Return the selectable scope (profile) names, if any."""
        ...

    def scope_data(self, raw: dict[str, Any], name: str) -> dict[str, Any] | None:
        """Return one scope's raw values, or ``None`` when undefined."""
        ...

    def write_scope(
        self, raw: dict[str, Any], requested: str | None
    ) -> tuple[dict[str, Any], str | None]:
        """Return ``(mutable target dict, scope name)`` for settings writes.

        The scope name is ``None`` for layouts without scopes (writes land
        at the top level). A ``requested`` scope the layout cannot satisfy
        raises ``ConfigError``.
        """
        ...


class ProfilesSettingsLayout:
    """Built-in layout that layers ``profiles.default`` beneath ``profiles.<active>``.

    Profiles are a first-class SDK capability, so this layout (and the
    resolver it delegates to) lives in core. ``run_tool`` registers it for
    every tool, and it is the registry default when none is registered.
    """

    supports_scopes = True

    def effective(self, raw: dict[str, Any], *, scope: str | None = None) -> dict[str, Any]:
        effective, _ = self._resolve(raw, scope)
        return effective

    def provenance(self, raw: dict[str, Any]) -> dict[tuple[str, ...], str]:
        _, provenance = self._resolve(raw)
        return provenance

    def _resolve(
        self, raw: dict[str, Any], scope: str | None = None
    ) -> tuple[dict[str, Any], dict[tuple[str, ...], str]]:
        """Resolve effective settings + provenance for the active (or given) scope."""
        override = scope or effective_active_profile_name(raw)
        return resolve_profiles(raw, active_override=override)

    def scope_names(self, raw: dict[str, Any]) -> list[str]:
        profiles = raw.get("profiles")
        return sorted(profiles) if isinstance(profiles, dict) else []

    def scope_data(self, raw: dict[str, Any], name: str) -> dict[str, Any] | None:
        profiles = raw.get("profiles")
        if not isinstance(profiles, dict):
            return None
        data = profiles.get(name)
        return data if isinstance(data, dict) else None

    def write_scope(self, raw: dict[str, Any], requested: str | None) -> tuple[dict[str, Any], str]:
        """Return the target profile's dict, creating only ``default``.

        Any other target must already exist — this is the guardrail that
        keeps ``config set --target-profile typo`` from silently creating a
        new profile.
        """
        name = requested or effective_active_profile_name(raw) or DEFAULT_PROFILE
        existing = raw.get("profiles")
        known = existing if isinstance(existing, dict) else {}
        if name != DEFAULT_PROFILE and name not in known:
            known_str = ", ".join(sorted(known)) or "(none)"
            command = current_tool_command() or "<tool>"
            raise ConfigError(
                f"profile {name!r} does not exist; known profiles: {known_str}. "
                f"Create it first with `{command} profile create`."
            )
        profiles = raw.setdefault("profiles", {})
        if not isinstance(profiles, dict):
            raise ConfigError("config key 'profiles' must be a mapping")
        target = profiles.setdefault(name, {})
        if not isinstance(target, dict):
            raise ConfigError(f"profile {name!r} must be a mapping")
        return target, name
