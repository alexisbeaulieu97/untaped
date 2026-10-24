"""YAML-backed adapter for profile reader and writer ports.

This is the only profile module that knows about the on-disk layout — the
use cases stay portable. It builds on the SDK's config primitives
(``read_config_dict`` / ``mutate_config``); the profile-shaped editing
lives here. The base (``config.yml``'s top level) reads as the profile
``default``.
"""

from __future__ import annotations

from typing import Any

from untaped.runtime.config_file import mutate_config, read_config_dict
from untaped.runtime.profile_resolver import (
    ProfileSource,
    classify_active_profile,
    effective_active_profile_name,
)
from untaped.runtime.settings import active_settings_layout


def _profiles(config: dict[str, Any]) -> dict[str, Any]:
    """Return the ``profiles`` mapping from a config dict, or empty if absent/malformed."""
    profiles = config.get("profiles")
    return profiles if isinstance(profiles, dict) else {}


class ProfileFileRepository:
    """Concrete adapter over ``~/.untaped/config.yml``."""

    def names(self) -> list[str]:
        """``default`` (the base), then the named profiles."""
        return active_settings_layout().profile_names(read_config_dict())

    def active_name(self) -> str | None:
        """Return the picked profile: ``--profile``, else ``UNTAPED_PROFILE``."""
        return effective_active_profile_name()

    def classify_active(self) -> tuple[str | None, ProfileSource]:
        """Return the picked profile name + what picked it."""
        return classify_active_profile()

    def read(self, name: str) -> dict[str, Any] | None:
        return active_settings_layout().profile_data(read_config_dict(), name)

    def resolved(self, name: str) -> dict[str, Any]:
        """Return the base ⤥ ``name`` as a merged dict (empty if neither sets anything).

        Renamed keys show under their current names.
        """
        return active_settings_layout().effective(read_config_dict(), profile=name)

    def write(self, name: str, data: dict[str, Any]) -> None:
        def _apply(config: dict[str, Any]) -> None:
            profiles = config.get("profiles")
            if not isinstance(profiles, dict):
                profiles = {}
                config["profiles"] = profiles
            profiles[name] = data

        mutate_config(_apply)

    def delete(self, name: str) -> bool:
        removed = False

        def _apply(config: dict[str, Any]) -> None:
            nonlocal removed
            profiles = _profiles(config)
            if name not in profiles:
                return
            del profiles[name]
            removed = True

        mutate_config(_apply)
        return removed

    def rename(self, old: str, new: str) -> None:
        """Rename ``profiles.<old>`` to ``profiles.<new>`` in one transaction.

        Raises ``KeyError`` if ``old`` is missing or ``ValueError`` if ``new``
        already exists, both *before* any mutation is written.
        """

        def _apply(config: dict[str, Any]) -> None:
            profiles = _profiles(config)
            if old not in profiles:
                raise KeyError(f"profile {old!r} does not exist")
            if new in profiles:
                raise ValueError(f"profile {new!r} already exists")
            profiles[new] = profiles.pop(old)

        mutate_config(_apply)
