"""YAML-backed adapter for profile reader and writer ports.

This is the only profile module that knows about the on-disk layout — the
use cases stay portable. It builds on the SDK's config primitives
(``read_config_dict`` / ``mutate_config``); the profile-shaped editing
lives here.
"""

from __future__ import annotations

from typing import Any

from untaped.config_file import mutate_config, read_config_dict
from untaped.profile_resolver import (
    ProfileSource,
    classify_active_profile,
    effective_active_profile_name,
    resolve_profiles,
)


def _profiles(config: dict[str, Any]) -> dict[str, Any]:
    """Return the ``profiles`` mapping from a config dict, or empty if absent/malformed."""
    profiles = config.get("profiles")
    return profiles if isinstance(profiles, dict) else {}


class ProfileFileRepository:
    """Concrete adapter over ``~/.untaped/config.yml``."""

    def names(self) -> list[str]:
        return list(_profiles(read_config_dict()).keys())

    def active_name(self) -> str | None:
        """Return the effective active profile, honouring ``UNTAPED_PROFILE``.

        The env override mirrors what every other consumer of "active
        profile" sees (``config list``, the profiles settings layout,
        etc.), so the ``profile list`` ✓ marker stays consistent with
        reality during a per-call override.
        """
        return effective_active_profile_name(read_config_dict())

    def persisted_active_name(self) -> str | None:
        """Return ``active:`` from disk, ignoring per-call overrides.

        Mutating use cases (delete, rename) compare against this so a
        transient ``--profile`` option never rewrites the user's persisted
        active pointer behind their back.
        """
        name = read_config_dict().get("active")
        return name if isinstance(name, str) and name else None

    def classify_active(self) -> tuple[str | None, ProfileSource]:
        """Return the effective active profile name + the layer that supplied it.

        Delegates to the domain resolver, which is the single source of
        truth for the env/active/fallback precedence.
        """
        return classify_active_profile(read_config_dict())

    def read(self, name: str) -> dict[str, Any] | None:
        profile = _profiles(read_config_dict()).get(name)
        return profile if isinstance(profile, dict) else None

    def resolved(self, name: str) -> dict[str, Any]:
        """Return ``default`` ⤥ ``name`` as a merged dict (empty if neither set)."""
        effective, _ = resolve_profiles(read_config_dict(), active_override=name)
        return effective

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

        Also updates ``active:`` if it pointed at ``old``. Raises
        ``KeyError`` if ``old`` is missing or ``ValueError`` if ``new``
        already exists, both *before* any mutation is written.
        """

        def _apply(config: dict[str, Any]) -> None:
            profiles = _profiles(config)
            if old not in profiles:
                raise KeyError(f"profile {old!r} does not exist")
            if new in profiles:
                raise ValueError(f"profile {new!r} already exists")
            profiles[new] = profiles.pop(old)
            if config.get("active") == old:
                config["active"] = new

        mutate_config(_apply)

    def set_active(self, name: str) -> None:
        def _apply(config: dict[str, Any]) -> None:
            config["active"] = name

        mutate_config(_apply)
