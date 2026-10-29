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
from untaped.runtime.profile_resolver import Picked, check_defined, current_pick, picked_names
from untaped.runtime.settings import active_settings_layout
from untaped.sdk.errors import ConfigError


def _profiles(config: dict[str, Any]) -> dict[str, Any]:
    """Return the ``profiles`` mapping from a config dict, or empty if absent/malformed."""
    profiles = config.get("profiles")
    return profiles if isinstance(profiles, dict) else {}


def _keep_pick(config: dict[str, Any], *, losing: str | None = None) -> None:
    """Refuse a change that leaves this run's pick naming an undefined profile.

    ``losing`` is the profile the change removes; picking it is refused by
    name. Called on the changed dict, so nothing is written when it raises.
    """
    if losing is not None and losing in picked_names():
        raise ConfigError(
            f"profile {losing!r} is in this run's pick",
            category="usage",
            hint="run it without that profile in UNTAPED_PROFILE or --profile",
        )
    check_defined(picked_names(), _profiles(config))


class ProfileFileRepository:
    """Concrete adapter over ``~/.untaped/config.yml``."""

    def names(self) -> list[str]:
        """``default`` (the base), then the named profiles."""
        return active_settings_layout().profile_names(read_config_dict())

    def pick(self) -> tuple[Picked, ...]:
        """The run's pick: ``UNTAPED_PROFILE``'s profiles, then ``--profile``'s; each defined."""
        picks = current_pick()
        check_defined([pick.name for pick in picks], _profiles(read_config_dict()))
        return picks

    def read(self, name: str) -> dict[str, Any] | None:
        return active_settings_layout().profile_data(read_config_dict(), name)

    def resolved(self, name: str | None) -> dict[str, Any]:
        """Return the base ⤥ ``name`` (``None``: the run's pick) as a merged dict.

        Empty if nothing sets anything. Renamed keys show under their current names.
        """
        return active_settings_layout().effective(read_config_dict(), profile=name)

    def write(self, name: str, data: dict[str, Any]) -> None:
        def _apply(config: dict[str, Any]) -> None:
            profiles = config.get("profiles")
            if not isinstance(profiles, dict):
                profiles = {}
                config["profiles"] = profiles
            profiles[name] = data
            _keep_pick(config)

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
            _keep_pick(config, losing=name)

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
            _keep_pick(config, losing=old)

        mutate_config(_apply)
