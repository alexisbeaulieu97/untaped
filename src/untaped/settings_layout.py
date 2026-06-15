"""Layout protocol mapping the raw config file to effective settings.

A *settings layout* decides how the parsed ``~/.untaped/config.yml`` dict
becomes the effective settings values: flat top-level keys by default, or a
plugin-contributed scheme (the untaped-profile plugin layers
``profiles.default`` and ``profiles.<active>``). At most one plugin may
contribute a layout via ``PluginManifest.settings_layout``.
"""

from __future__ import annotations

import sys
from typing import Any, Protocol, runtime_checkable

from untaped.errors import ConfigError
from untaped.profile_resolver import (
    DEFAULT_PROFILE,
    effective_active_profile_name,
    resolve_profiles,
)

#: Top-level keys reserved for the profile scheme; the flat layout ignores
#: them (with a warning) so an uninstalled profile plugin cannot brick a CLI.
PROFILE_LAYOUT_KEYS = ("profiles", "active")


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


_warned_about_profile_keys = False


class FlatSettingsLayout:
    """Default layout: top-level config keys are the settings values."""

    supports_scopes = False

    def effective(self, raw: dict[str, Any], *, scope: str | None = None) -> dict[str, Any]:
        if any(key in raw for key in PROFILE_LAYOUT_KEYS):
            _warn_profile_keys_ignored()
        return {key: value for key, value in raw.items() if key not in PROFILE_LAYOUT_KEYS}

    def provenance(self, raw: dict[str, Any]) -> dict[tuple[str, ...], str]:
        leaves: dict[tuple[str, ...], str] = {}
        _collect_leaves(self.effective(raw), (), leaves)
        return leaves

    def scope_names(self, raw: dict[str, Any]) -> list[str]:
        return []

    def scope_data(self, raw: dict[str, Any], name: str) -> dict[str, Any] | None:
        return None

    def write_scope(
        self, raw: dict[str, Any], requested: str | None
    ) -> tuple[dict[str, Any], str | None]:
        if requested is not None:
            raise ConfigError(
                f"profile {requested!r} was requested but profiles are not available; "
                "install the untaped-profile plugin"
            )
        return raw, None


def _collect_leaves(
    data: dict[str, Any],
    path: tuple[str, ...],
    out: dict[tuple[str, ...], str],
) -> None:
    for key, value in data.items():
        leaf_path = (*path, key)
        if isinstance(value, dict):
            _collect_leaves(value, leaf_path, out)
        else:
            out[leaf_path] = "config"


def _warn_profile_keys_ignored() -> None:
    global _warned_about_profile_keys
    if _warned_about_profile_keys:
        return
    _warned_about_profile_keys = True
    # Plain print keeps this module free of CLI imports; stderr keeps
    # structured stdout (--format json) intact.
    print(
        "warning: config defines profiles but the untaped-profile plugin is not "
        "installed; profile values are ignored (untaped plugins add untaped-profile)",
        file=sys.stderr,
    )


def reset_flat_layout_warning_for_tests() -> None:
    """Reset the warn-once latch. Public only for tests."""
    global _warned_about_profile_keys
    _warned_about_profile_keys = False


class ProfilesSettingsLayout:
    """Built-in layout that layers ``profiles.default`` beneath ``profiles.<active>``.

    Profiles are a first-class SDK capability, so this layout (and the
    resolver it delegates to) lives in core. ``run_tool`` selects it for
    every tool; it supersedes :class:`FlatSettingsLayout`, which remains
    only for the legacy plugin runtime until that machinery is retired.
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
            raise ConfigError(
                f"profile {name!r} does not exist; known profiles: {known_str}. "
                "Create it first with `untaped profile create`."
            )
        profiles = raw.setdefault("profiles", {})
        if not isinstance(profiles, dict):
            raise ConfigError("config key 'profiles' must be a mapping")
        target = profiles.setdefault(name, {})
        if not isinstance(target, dict):
            raise ConfigError(f"profile {name!r} must be a mapping")
        return target, name
