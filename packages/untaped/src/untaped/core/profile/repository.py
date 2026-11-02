"""YAML-backed adapter for profile reader and writer ports.

This is the only profile module that knows about the on-disk layout — the
use cases stay portable. It builds on the SDK's config primitives
(``read_config_dict`` / ``mutate_config``); the profile-shaped editing
lives here. The base (``config.yml``'s top level) reads as the profile
``default``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from untaped.runtime.config_file import mutate_config, read_config_dict
from untaped.runtime.config_read import load_project_config
from untaped.runtime.profile_resolver import (
    Picked,
    check_defined,
    current_pick,
    explicit_pick,
    pick_names,
)
from untaped.runtime.settings import active_settings_layout
from untaped.sdk.errors import ConfigError, UntapedError

if TYPE_CHECKING:
    from untaped.core.profile.project_picks import ProjectPick


def _profiles(config: dict[str, Any]) -> dict[str, Any]:
    """Return the ``profiles`` mapping from a config dict, or empty if absent/malformed."""
    profiles = config.get("profiles")
    return profiles if isinstance(profiles, dict) else {}


def _keep_pick(config: dict[str, Any], *, losing: str | None = None) -> None:
    """Refuse a change that leaves this run's own pick naming an undefined profile.

    ``losing`` is the profile the change removes; picking it is refused by
    name. Called on the changed dict, so nothing is written when it raises.
    A project's pick is not the run's own: the change rewrites it.
    """
    if losing is not None and losing in explicit_pick():
        raise ConfigError(
            f"profile {losing!r} is in this run's pick",
            category="usage",
            hint="run it without that profile in UNTAPED_PROFILE or --profile",
        )
    check_defined(explicit_pick(), _profiles(config))


class ProfileFileRepository:
    """Concrete adapter over ``~/.untaped/config.yml``."""

    def names(self) -> list[str]:
        """``default`` (the base), then the named profiles."""
        return active_settings_layout().profile_names(read_config_dict())

    def pick(self) -> tuple[Picked, ...]:
        """The run's pick: the project's, ``UNTAPED_PROFILE``'s, ``--profile``'s; each defined."""
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


# --------------------------------------------------------------------------- #
# A project's ``profile:``
# --------------------------------------------------------------------------- #


def rewrite_project_picks(
    found: Sequence[ProjectPick], old: str, new: str | None
) -> list[ProjectPick]:
    """Rename ``old`` to ``new`` in each project's ``profile:``, or drop it (``new`` ``None``).

    Returns the projects left as they were: those with a problem, and those
    whose write failed (the failure is the problem).
    """
    from untaped.core.profile.project_picks import ProjectPick  # noqa: PLC0415

    left: list[ProjectPick] = []
    for each in found:
        if each.problem is not None:
            left.append(each)
            continue
        try:
            mutate_config(_renaming(old, new), each.file, load=load_project_config)
        except (UntapedError, OSError) as exc:  # the home's rename is done: report, never crash
            left.append(ProjectPick(each.name, each.file, str(exc)))
    return left


def _renaming(old: str, new: str | None) -> Callable[[dict[str, Any]], None]:
    def apply(data: dict[str, Any]) -> None:
        value = data.get("profile")
        if not isinstance(value, str | list):
            return
        names = [new if name == old else name for name in pick_names(value)]
        kept = list(dict.fromkeys(name for name in names if name is not None))
        if not kept:
            data.pop("profile", None)
        elif isinstance(value, str) and len(kept) == 1:
            data["profile"] = kept[0]
        else:
            data["profile"] = kept

    return apply
