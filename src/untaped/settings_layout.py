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
