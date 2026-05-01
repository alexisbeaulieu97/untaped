"""YAML-backed adapter that satisfies :class:`ProfileRepository`.

This is the only profile module that knows about the on-disk layout — the
use cases stay portable. Every method delegates to ``untaped_core``'s
profile-aware helpers in ``config_file`` and ``profile_resolver``.
"""

from __future__ import annotations

from typing import Any

from untaped_core import get_settings
from untaped_core.config_file import (
    delete_profile,
    get_active_profile_name,
    list_profile_names,
    read_config_dict,
    read_profile,
    rename_profile,
    set_active_profile,
    write_profile,
)
from untaped_core.profile_resolver import effective_active_profile_name, resolve_profiles


class ProfileFileRepository:
    """Concrete adapter over ``~/.untaped/config.yml``."""

    def names(self) -> list[str]:
        return list_profile_names()

    def active_name(self) -> str | None:
        """Return the effective active profile, honouring ``UNTAPED_PROFILE``.

        The env override mirrors what every other consumer of "active
        profile" sees (``untaped config list``, the resolver inside
        :class:`ProfilesSettingsSource`, etc.), so the ``profile list`` ✓
        marker stays consistent with reality during a per-call override.
        """
        return effective_active_profile_name(read_config_dict())

    def persisted_active_name(self) -> str | None:
        """Return ``active:`` from disk, ignoring per-call overrides.

        Mutating use cases (delete, rename) compare against this so a
        transient ``--profile`` flag never rewrites the user's persisted
        active pointer behind their back.
        """
        return get_active_profile_name()

    def read(self, name: str) -> dict[str, Any] | None:
        return read_profile(name)

    def resolved(self, name: str) -> dict[str, Any]:
        """Return ``default`` ⤥ ``name`` as a merged dict (empty if neither set)."""
        effective, _ = resolve_profiles(read_config_dict(), active_override=name)
        return effective

    def write(self, name: str, data: dict[str, Any]) -> None:
        write_profile(name, data)
        get_settings.cache_clear()

    def delete(self, name: str) -> bool:
        removed = delete_profile(name)
        if removed:
            get_settings.cache_clear()
        return removed

    def rename(self, old: str, new: str) -> None:
        rename_profile(old, new)
        get_settings.cache_clear()

    def set_active(self, name: str) -> None:
        set_active_profile(name)
        get_settings.cache_clear()
