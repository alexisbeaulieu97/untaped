"""Pure helpers that split ``config.yml`` (format 2) and layer a profile over the base.

Profiles are a core feature, not a plugin: this resolver and
:class:`untaped.runtime.settings_layout.ProfilesSettingsLayout` are core's home for
profile resolution.

The file's own keys are :data:`FILE_KEYS` (``format_version`` and
``profiles``); every other top-level key is a section of the **base**, which
every run reads. A run picks named profiles (``UNTAPED_PROFILE``'s comma
list, then the run's ``--profile`` pick), layered over the base in order.
:func:`resolve_profiles` returns:

- ``effective``: a deep-merged dict of values (a later pick beats an earlier
  one, and every pick beats the base, per leaf).
- ``provenance``: a flat ``leaf_path -> profile_name`` map naming the profile
  that supplied each leaf in ``effective`` (``default`` for the base).
  Missing entries mean the leaf came from the schema default.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from untaped.sdk._run import run_or_none
from untaped.sdk.errors import ConfigError, UsageError
from untaped.sdk.words import not_found

ACTIVE_PROFILE_ENV = "UNTAPED_PROFILE"
#: The base's name: what ``--profile default`` picks and what provenance calls it.
DEFAULT_PROFILE = "default"

#: What picked a profile: the root ``--profile`` option or ``UNTAPED_PROFILE``.
PickSource = Literal["flag", "env"]

#: A profile name: letters, digits and ``_`` first, then also ``.`` and ``-``.
PROFILE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")


@dataclass(frozen=True, slots=True)
class Picked:
    """One profile of the run's pick and what picked it."""

    name: str
    source: PickSource


#: ``config.yml``'s own top-level keys (format 2); every other key is a base section.
FILE_KEYS = frozenset({"format_version", "profiles"})


def parse_pick(text: str, *, origin: str) -> tuple[str, ...]:
    """The profile names a comma list names, in order; ``origin`` names it in errors.

    Raises :class:`ConfigError` (a usage error, exit 2) for an empty or
    malformed name, and for ``default`` (the base alone) inside a longer list.
    """
    names = tuple(part.strip() for part in text.split(","))
    for name in names:
        if not PROFILE_NAME.fullmatch(name):
            raise ConfigError(
                f"invalid profile name {name!r} in {origin}",
                category="usage",
                hint=(
                    "a name is letters, digits, `_`, `.` and `-`, and does not start with `.` "
                    "or `-`; separate names with commas"
                ),
            )
    check_pick(names, origin=origin)
    return names


def check_pick(names: Sequence[str], *, origin: str) -> None:
    """Refuse ``default`` (the base alone) stacked with other profiles."""
    if DEFAULT_PROFILE in names and len(names) > 1:
        raise ConfigError(
            f"{origin} stacks `default` with other profiles; `default` means the base alone",
            category="usage",
            hint="leave `default` out: the base is always read beneath the picked profiles",
        )


def pick_names(profiles: str | Sequence[str]) -> tuple[str, ...]:
    """A pick's profile names, in order: a string is one profile, never its letters."""
    return (profiles,) if isinstance(profiles, str) else tuple(profiles)


def flag_pick() -> tuple[str, ...]:
    """The names ``--profile`` picks for the current run, in order (no run: none)."""
    run = run_or_none()
    return () if run is None else run.profiles


def current_pick() -> tuple[Picked, ...]:
    """The profiles this run layers over the base, in order (later wins).

    ``UNTAPED_PROFILE``'s names, then ``--profile``'s; a name picked twice
    keeps its last place. ``--profile default`` sets the variable aside and
    reads the base alone; ``UNTAPED_PROFILE=default`` adds nothing.
    """
    flag = flag_pick()
    if flag == (DEFAULT_PROFILE,):
        return ()
    text = os.environ.get(ACTIVE_PROFILE_ENV)
    env = parse_pick(text, origin=ACTIVE_PROFILE_ENV) if text else ()
    if env == (DEFAULT_PROFILE,):
        env = ()
    picks = [Picked(name, "env") for name in env] + [Picked(name, "flag") for name in flag]
    last = {pick.name: index for index, pick in enumerate(picks)}
    return tuple(pick for index, pick in enumerate(picks) if last[pick.name] == index)


def picked_names() -> tuple[str, ...]:
    """The names of :func:`current_pick`."""
    return tuple(pick.name for pick in current_pick())


def selected_profile() -> str:
    """The run's pick as one ``--profile`` value: the names comma-joined, else ``default``."""
    return ",".join(picked_names()) or DEFAULT_PROFILE


def write_target() -> str:
    """The profile a write lands in: the one ``--profile`` names, else the base.

    ``UNTAPED_PROFILE`` picks what runs read, never where they write.
    Raises :class:`UsageError` when ``--profile`` names more than one.
    """
    flag = flag_pick()
    if len(flag) > 1:
        raise UsageError(
            f"--profile {','.join(flag)} names {len(flag)} profiles; a write needs one",
            hint="name the profile to write with `--profile NAME`",
            details={"profiles": list(flag)},
        )
    return flag[0] if flag else DEFAULT_PROFILE


def fix_target() -> str:
    """The one profile a fix line writes to so this run reads it: the last pick, else the base.

    A fix line names it with ``--profile``, which wins over ``UNTAPED_PROFILE``'s
    names, so the line works typed in the same shell.
    """
    names = picked_names()
    return names[-1] if names else DEFAULT_PROFILE


def base_sections(config_data: dict[str, Any]) -> dict[str, Any]:
    """The base: every top-level key of ``config.yml`` that is not one of :data:`FILE_KEYS`."""
    return {key: value for key, value in config_data.items() if key not in FILE_KEYS}


def named_profiles(config_data: dict[str, Any]) -> dict[str, Any]:
    """The ``profiles`` mapping, checked; ``{}`` when absent.

    Raises ``ConfigError`` when ``profiles`` (or one profile) is not a
    mapping, or when it names ``default`` (the base is the top level). An
    empty ``name:`` entry (YAML ``null``) is an empty profile.
    """
    profiles = config_data.get("profiles")
    if profiles is None:
        return {}
    if not isinstance(profiles, dict):
        raise ConfigError(
            f"config key 'profiles' must be a mapping of profile names, "
            f"got {type(profiles).__name__}"
        )
    if DEFAULT_PROFILE in profiles:
        raise ConfigError(
            "profiles.default is not read: the base is config.yml's top level",
            hint="move its sections to the top of the file",
        )
    for name, data in profiles.items():
        if data is not None and not isinstance(data, dict):
            raise ConfigError(f"profile {name!r} must be a mapping, got {type(data).__name__}")
    return profiles


def profile_path(name: str) -> tuple[str, ...]:
    """Where profile ``name``'s sections sit in ``config.yml``: the top level for the base."""
    return () if name == DEFAULT_PROFILE else ("profiles", name)


def profile_entries(
    config_data: dict[str, Any],
) -> Iterator[tuple[str, dict[str, Any], tuple[str, ...]]]:
    """``(name, mapping, path)`` for the base, then each named profile by name.

    ``mapping`` is the dict in ``config_data`` itself, so a caller may change
    it in place: ``config_data`` for the base (its :data:`FILE_KEYS` included,
    so walk sections, not keys), ``profiles.<name>`` otherwise. ``path`` is
    where the mapping sits in the file. A malformed profile is skipped.
    """
    yield DEFAULT_PROFILE, config_data, ()
    profiles = config_data.get("profiles")
    if not isinstance(profiles, dict):
        return
    for name in sorted(profiles, key=str):
        data = profiles[name]
        if isinstance(data, dict):
            yield str(name), data, profile_path(str(name))


def resolve_profiles(
    config_data: dict[str, Any],
    *,
    picks: Sequence[str] = (),
) -> tuple[dict[str, Any], dict[tuple[str, ...], str]]:
    """Return ``(effective, provenance)`` from the parsed config dict.

    The base is layered first, then each of ``picks`` in order (``default``
    is the base, already layered). Raises ``ConfigError`` as
    :func:`named_profiles` does, and a usage ``ConfigError`` (exit 2, with
    ``details.known``) when a pick names an undefined profile.
    """
    profiles = named_profiles(config_data)
    check_defined(picks, profiles)
    effective: dict[str, Any] = {}
    provenance: dict[tuple[str, ...], str] = {}
    _layer(base_sections(config_data), DEFAULT_PROFILE, effective, provenance, ())
    for name in picks:
        if name != DEFAULT_PROFILE:
            _layer(profiles[name] or {}, name, effective, provenance, ())
    return effective, provenance


def check_defined(picks: Sequence[str], profiles: dict[str, Any]) -> None:
    """Raise a usage ``ConfigError`` (exit 2, ``details.known``) for a pick not in ``profiles``.

    ``profiles`` is :func:`named_profiles`' mapping; ``default`` is always defined.
    """
    for name in picks:
        if name != DEFAULT_PROFILE and name not in profiles:
            known = [DEFAULT_PROFILE, *sorted(profiles)]
            raise ConfigError(
                not_found("profile", name, known=known),
                category="usage",
                details={"known": known},
            )


def _layer(
    src: dict[str, Any],
    profile: str,
    dst: dict[str, Any],
    provenance: dict[tuple[str, ...], str],
    path: tuple[str, ...],
) -> None:
    """Deep-merge ``src`` into ``dst`` and record per-leaf provenance.

    Lists and other non-dict values replace the corresponding key wholesale
    (no list-element merging). Nested dicts recurse.
    """
    for key, value in src.items():
        leaf_path = (*path, key)
        if isinstance(value, dict):
            existing = dst.get(key)
            child = existing if isinstance(existing, dict) else {}
            dst[key] = child
            _layer(value, profile, child, provenance, leaf_path)
        else:
            dst[key] = value
            provenance[leaf_path] = profile
