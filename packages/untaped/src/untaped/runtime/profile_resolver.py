"""Pure helpers that split ``config.yml`` (format 2) and layer a profile over the base.

Profiles are a core feature, not a plugin: this resolver and
:class:`untaped.runtime.settings_layout.ProfilesSettingsLayout` are core's home for
profile resolution.

The file's own keys are :data:`FILE_KEYS` (``format_version`` and
``profiles``); every other top-level key is a section of the **base**, which
every run reads. A run picks named profiles (its project's ``profile:``,
then ``UNTAPED_PROFILE``'s comma list, then the run's ``--profile`` pick),
layered over the base in order; the project's own values sit just above
its picks (:mod:`untaped.runtime.folder_layer`).
:func:`resolve_profiles` returns:

- ``effective``: a deep-merged dict of values (a later pick beats an earlier
  one, and every pick beats the base, per leaf).
- ``provenance``: a flat ``leaf_path -> profile_name`` map naming the profile
  that supplied each leaf in ``effective`` (``default`` for the base;
  ``project:<name>`` for a project's own values, a spelling no profile name
  can take). Missing entries mean the leaf came from the schema default.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from untaped.sdk._run import run_or_none
from untaped.sdk.errors import ConfigError, UsageError
from untaped.sdk.words import not_found

if TYPE_CHECKING:
    from untaped.runtime.folder_layer import FolderLayer

ACTIVE_PROFILE_ENV = "UNTAPED_PROFILE"
#: The base's name: what ``--profile default`` picks and what provenance calls it.
DEFAULT_PROFILE = "default"

#: What picked a profile: the project's ``profile:``, ``UNTAPED_PROFILE`` or ``--profile``.
PickSource = Literal["project", "env", "flag"]

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
    check_names(names, origin=origin, hint="; separate names with commas")
    return names


def check_names(
    names: Sequence[str], *, origin: str, category: str = "usage", hint: str = ""
) -> None:
    """Refuse a malformed profile name, and ``default`` stacked with other profiles.

    ``category`` is the error's (``usage`` for a typed pick, exit 2; ``config``
    for a file's, exit 4); ``hint`` is added to the name rule's hint.
    """
    for name in names:
        if not PROFILE_NAME.fullmatch(name):
            raise ConfigError(
                f"invalid profile name {name!r} in {origin}",
                category=category,
                hint=(
                    "a name is letters, digits, `_`, `.` and `-`, and does not start with `.` "
                    f"or `-`{hint}"
                ),
            )
    check_pick(names, origin=origin, category=category)


def check_pick(names: Sequence[str], *, origin: str, category: str = "usage") -> None:
    """Refuse ``default`` (the base alone) stacked with other profiles."""
    if DEFAULT_PROFILE in names and len(names) > 1:
        raise ConfigError(
            f"{origin} stacks `default` with other profiles; `default` means the base alone",
            category=category,
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

    The project's ``profile:`` names the home defines, then
    ``UNTAPED_PROFILE``'s, then ``--profile``'s; a name picked twice keeps
    its last place. ``--profile default`` sets the others aside and reads
    the base alone; ``UNTAPED_PROFILE=default`` sets the project's aside.
    """
    return run_layers()[0]


def run_layers() -> tuple[tuple[Picked, ...], FolderLayer | None]:
    """:func:`current_pick`, and the project's layer whose own values this run reads.

    The project's values sit above its picks and beneath the others, so
    they stay read under ``--profile default``, which sets its picks aside.
    """
    from untaped.runtime.folder_layer import folder_layer  # noqa: PLC0415 - it imports this

    folder = folder_layer()
    env, flag = _run_picks()
    if flag == (DEFAULT_PROFILE,):
        return (), folder
    project = () if folder is None or env == (DEFAULT_PROFILE,) else folder.picks
    if folder is not None and not folder_picks_aside():
        folder.warn_skipped()
    if env == (DEFAULT_PROFILE,):
        env = ()
    picks = [
        *(Picked(name, "project") for name in project),
        *(Picked(name, "env") for name in env),
        *(Picked(name, "flag") for name in flag),
    ]
    last = {pick.name: index for index, pick in enumerate(picks)}
    return tuple(pick for index, pick in enumerate(picks) if last[pick.name] == index), folder


def explicit_pick() -> tuple[str, ...]:
    """The names the run itself picks: ``UNTAPED_PROFILE``'s, then ``--profile``'s, no project's.

    What ``project init`` records as a new project's ``profile:``; ``()``
    when nothing picks, or the pick is ``default`` (the base alone).
    """
    env, flag = _run_picks()
    if flag == (DEFAULT_PROFILE,):
        return ()
    names = [*(() if env == (DEFAULT_PROFILE,) else env), *flag]
    last = {name: index for index, name in enumerate(names)}
    return tuple(name for index, name in enumerate(names) if last[name] == index)


def folder_picks_aside() -> bool:
    """Whether ``default`` (``--profile`` or ``UNTAPED_PROFILE``) sets the project's picks aside."""
    env, flag = _run_picks()
    return (DEFAULT_PROFILE,) in (env, flag)


def _run_picks() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``UNTAPED_PROFILE``'s names and ``--profile``'s, as written; no project's file is read."""
    text = os.environ.get(ACTIVE_PROFILE_ENV)
    env = parse_pick(text, origin=ACTIVE_PROFILE_ENV) if text else ()
    return env, flag_pick()


def settings_key() -> tuple[object, ...]:
    """What a run's settings depend on beyond the home: its pick and its project's file.

    Caches of resolved settings key on it, so a run in another project, or
    after its project's file changed, reads its own.
    """
    picks, folder = run_layers()
    return (
        tuple(pick.name for pick in picks),
        None if folder is None else (folder.file, folder.stamp),
    )


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


@dataclass(frozen=True)
class FolderValues:
    """A project's own values, layered after the first ``at`` picks (its own)."""

    at: int
    source: str
    """Their provenance: ``project:<name>``."""
    sections: dict[str, Any]


def resolve_profiles(
    config_data: dict[str, Any],
    *,
    picks: Sequence[str] = (),
    folder: FolderValues | None = None,
) -> tuple[dict[str, Any], dict[tuple[str, ...], str]]:
    """Return ``(effective, provenance)`` from the parsed config dict.

    The base is layered first, then each of ``picks`` in order (``default``
    is the base, already layered), with ``folder``'s values after its first
    ``folder.at`` picks. Raises ``ConfigError`` as :func:`named_profiles`
    does, and a usage ``ConfigError`` (exit 2, with ``details.known``) when
    a pick names an undefined profile.
    """
    profiles = named_profiles(config_data)
    check_defined(picks, profiles)
    effective: dict[str, Any] = {}
    provenance: dict[tuple[str, ...], str] = {}
    _layer(base_sections(config_data), DEFAULT_PROFILE, effective, provenance, ())
    for index, name in enumerate((*picks, None)):
        if folder is not None and index == folder.at:
            _layer(folder.sections, folder.source, effective, provenance, ())
        if name is not None and name != DEFAULT_PROFILE:
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
    (no list-element merging). Nested dicts recurse; one that is still empty
    once merged is a leaf of its own (a map entry that holds nothing still sets
    the entry).
    """
    for key, value in src.items():
        leaf_path = (*path, key)
        if isinstance(value, dict):
            existing = dst.get(key)
            child = existing if isinstance(existing, dict) else {}
            dst[key] = child
            _layer(value, profile, child, provenance, leaf_path)
            if child:
                provenance.pop(leaf_path, None)  # its entries say where they come from
            else:
                provenance[leaf_path] = profile
        else:
            dst[key] = value
            provenance[leaf_path] = profile
