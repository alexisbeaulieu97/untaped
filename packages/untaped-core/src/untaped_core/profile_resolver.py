"""Pure helper that merges ``profiles.default`` with ``profiles.<active>``.

The resolver is layer-agnostic: callers (tests, ``ProfilesSettingsSource``,
``untaped config list``, ``untaped profile show``) hand it the parsed
``~/.untaped/config.yml`` dict and an optional ``active_override`` (set when
``UNTAPED_PROFILE`` or the root ``--profile`` flag is used). It returns:

- ``effective``: a deep-merged dict of values (active beats default per leaf).
- ``provenance``: a flat ``leaf_path -> profile_name`` map naming the profile
  that supplied each leaf in ``effective``. Missing entries mean the leaf
  came from the schema default (i.e. neither profile set it).

Top-level keys outside ``profiles`` are ignored — splicing app-state like
``workspace.workspaces`` back into the merged dict is
``ProfilesSettingsSource``'s responsibility, not ours.
"""

from __future__ import annotations

from typing import Any

from untaped_core.errors import ConfigError


def resolve_profiles(
    config_data: dict[str, Any],
    *,
    active_override: str | None = None,
) -> tuple[dict[str, Any], dict[tuple[str, ...], str]]:
    """Return ``(effective, provenance)`` from the parsed config dict."""
    profiles = config_data.get("profiles") or {}
    if not profiles:
        return {}, {}

    if "default" not in profiles:
        raise ConfigError(
            "config has a `profiles` section but no `default` profile; "
            "the default profile is required."
        )

    active_name = _select_active(config_data, active_override, profiles)

    effective: dict[str, Any] = {}
    provenance: dict[tuple[str, ...], str] = {}

    _layer(profiles["default"], "default", effective, provenance, ())
    if active_name != "default":
        _layer(profiles[active_name], active_name, effective, provenance, ())

    return effective, provenance


def _select_active(
    config_data: dict[str, Any],
    active_override: str | None,
    profiles: dict[str, Any],
) -> str:
    name = active_override if active_override else config_data.get("active") or "default"
    if name not in profiles:
        raise ConfigError(
            f"active profile {name!r} is not defined in `profiles`. "
            f"Known profiles: {', '.join(sorted(profiles))}"
        )
    return name


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
