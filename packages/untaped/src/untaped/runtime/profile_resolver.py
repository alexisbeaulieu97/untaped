"""Pure helpers that split ``config.yml`` (format 2) and layer a profile over the base.

Profiles are a core feature, not a plugin: this resolver and
:class:`untaped.runtime.settings_layout.ProfilesSettingsLayout` are core's home for
profile resolution.

The file's own keys are :data:`FILE_KEYS` (``format_version`` and
``profiles``); every other top-level key is a section of the **base**, which
every run reads. A run picks at most one named profile (the root
``--profile`` flag, else ``UNTAPED_PROFILE``), layered over the base.
:func:`resolve_profiles` returns:

- ``effective``: a deep-merged dict of values (the pick beats the base per leaf).
- ``provenance``: a flat ``leaf_path -> profile_name`` map naming the profile
  that supplied each leaf in ``effective`` (``default`` for the base).
  Missing entries mean the leaf came from the schema default.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal

from untaped.sdk.errors import ConfigError

ACTIVE_PROFILE_ENV = "UNTAPED_PROFILE"
#: The base's name: what ``--profile default`` picks and what provenance calls it.
DEFAULT_PROFILE = "default"

ProfileSource = Literal["flag", "env", "fallback"]


#: ``config.yml``'s own top-level keys (format 2); every other key is a base section.
FILE_KEYS = frozenset({"format_version", "profiles"})

_profile_override: ContextVar[str | None] = ContextVar("untaped_profile_override", default=None)


def set_profile_override(name: str | None) -> None:
    """Set (or clear, with ``None``) the invocation-scoped ``--profile`` override."""
    _profile_override.set(name)


@contextmanager
def profile_scope(name: str) -> Iterator[None]:
    """Select ``name`` as this invocation's profile inside the block.

    The root ``--profile`` option and ``setup`` both use it. The settings
    cache is dropped on entry and exit so reads follow the selection.
    """
    # Lazy: ``untaped.runtime.settings`` imports this module.
    from untaped.runtime.settings import get_settings  # noqa: PLC0415

    token = _profile_override.set(name)
    get_settings.cache_clear()
    try:
        yield
    finally:
        _profile_override.reset(token)
        get_settings.cache_clear()


def profile_override() -> str | None:
    """Return the active ``--profile`` override, if any."""
    return _profile_override.get()


def classify_active_profile() -> tuple[str | None, ProfileSource]:
    """Return the picked profile name **and** what picked it.

    ``--profile`` beats ``UNTAPED_PROFILE``; with neither, the run reads the
    base alone (``None``, ``fallback``). Powers ``profile current``.
    """
    override = _profile_override.get()
    if override:
        return override, "flag"
    env_override = os.environ.get(ACTIVE_PROFILE_ENV)
    if env_override:
        return env_override, "env"
    return None, "fallback"


def effective_active_profile_name() -> str | None:
    """Return the picked profile (``--profile``, else ``UNTAPED_PROFILE``), or ``None``."""
    name, _ = classify_active_profile()
    return name


def selected_profile() -> str:
    """The profile this invocation acts on: the pick, else ``default`` (the base)."""
    return effective_active_profile_name() or DEFAULT_PROFILE


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
    active_override: str | None = None,
) -> tuple[dict[str, Any], dict[tuple[str, ...], str]]:
    """Return ``(effective, provenance)`` from the parsed config dict.

    The base is layered first, then ``active_override`` when it names a
    profile (``default`` is the base alone). Raises ``ConfigError`` as
    :func:`named_profiles` does, or when the override names an undefined
    profile.
    """
    profiles = named_profiles(config_data)
    effective: dict[str, Any] = {}
    provenance: dict[tuple[str, ...], str] = {}
    _layer(base_sections(config_data), DEFAULT_PROFILE, effective, provenance, ())
    if active_override and active_override != DEFAULT_PROFILE:
        if active_override not in profiles:
            raise ConfigError(
                f"active profile {active_override!r} is not defined in `profiles`. "
                f"Known profiles: {', '.join(sorted(profiles)) or '(none)'}"
            )
        _layer(profiles[active_override] or {}, active_override, effective, provenance, ())
    return effective, provenance


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
