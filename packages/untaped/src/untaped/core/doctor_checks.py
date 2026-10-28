"""Core's own doctor checks: what ``untaped doctor`` says about core's config file.

``Core`` holds each :class:`~untaped.sdk.DoctorCheck` as an attribute, so its
row is ``untaped.<attribute>`` in kebab case (``untaped.config-permissions``),
run like any plugin's check, whose result names no id. Each reads
the config file itself when it runs; when the file doesn't load, the ``load
config file`` row fails and these report ``not checked``. Rows doctor derives
for every plugin (settings, state, setup, contracts, caches, skills) stay in
:mod:`untaped.core.commands.doctor`.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from untaped.runtime.config_file import read_config_dict
from untaped.runtime.profile_resolver import base_sections, profile_entries
from untaped.runtime.settings import (
    get_profile_settings_model,
    profile_section_models,
    resolve_config_path,
)
from untaped.sdk._schema import walk_settings
from untaped.sdk.errors import ConfigError
from untaped.sdk.health.deprecated_keys import key_mappings, scan_keys
from untaped.sdk.health.doctor import DoctorCheck, DoctorResult, PluginContext
from untaped.sdk.network._token_store import pass_problem

_NOT_CHECKED = "not checked: the config file doesn't load"


def _raw() -> dict[str, Any] | None:
    """The config file's data; ``None`` when it doesn't load (its own row fails)."""
    try:
        return read_config_dict()
    except ConfigError:
        return None


def _config_permissions(ctx: PluginContext) -> DoctorResult:
    """Warn when other users can read or write the config file (it may hold tokens)."""
    del ctx
    path = resolve_config_path()
    if os.name == "nt" or not path.is_file():
        return DoctorResult(ok=True, detail="not checked")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        detail = f"{path} has mode {mode:04o}; restrict it with `chmod 600 {path}`"
        return DoctorResult(ok=True, warn=True, detail=detail)
    return DoctorResult(ok=True, detail=f"mode {mode:04o}")


def _unknown_keys(ctx: PluginContext) -> DoctorResult:
    """Warn about keys no settings model reads, in the base and in each named profile.

    The top level is the base, so a section there no installed plugin reads
    (``log_level``, an uninstalled plugin's section) is flagged, not moved.
    """
    del ctx
    raw = _raw()
    if raw is None:
        return DoctorResult(ok=True, detail=_NOT_CHECKED)
    model = get_profile_settings_model()
    leaves = {d.path for d in walk_settings(model, include_collections=True)}
    # Old keys are the deprecated-keys row's to report; deleted ones are listed below.
    leaves |= {
        (section, *old.split("."))
        for section, section_model in profile_section_models().items()
        for old in (*key_mappings(section_model).migratable, *key_mappings(section_model).deleted)
    }
    prefixes = {path[:depth] for path in leaves for depth in range(1, len(path))}
    unknown: list[str] = []
    for _, data, location in profile_entries(dict(raw)):
        sections = base_sections(data) if not location else data
        _collect_unknown(sections, location, (), leaves, prefixes, unknown)
    deleted = [
        f"{item.section}.{item.old} (profile {item.profile}): {item.message}"
        for item in scan_keys(raw, profile_section_models())
        if item.kind == "deleted"
    ]
    parts = ([f"ignored: {', '.join(unknown)}{_other_readers()}"] if unknown else []) + deleted
    if not parts:
        return DoctorResult(ok=True, detail="no unknown keys")
    return DoctorResult(
        ok=True,
        warn=True,
        detail="; ".join(parts),
        fix="config migrate" if deleted else None,
        automatic=bool(deleted),
    )


def _token_store(ctx: PluginContext) -> DoctorResult:
    """Fail when a stored credential needs ``pass`` and it cannot work here (no key, no gpg).

    Checked only once a stored credential is in ``pass`` or reads through a
    ``pass`` command, so installing ``pass`` alone fails nothing.
    """
    del ctx
    from untaped.sdk.network._credential_store import rows  # noqa: PLC0415 - reads the index

    uses_pass = any(
        row.backend == "pass" or (row.argv and Path(row.argv[0]).name == "pass") for row in rows()
    )
    if not uses_pass:
        return DoctorResult(ok=True, detail="no stored credential uses pass")
    if (problem := pass_problem()) is not None:
        return DoctorResult(ok=False, detail=problem)
    return DoctorResult(ok=True, detail="gpg holds a key for the password store")


CONFIG_PERMISSIONS = DoctorCheck(title="config file permissions", run=_config_permissions)
UNKNOWN_KEYS = DoctorCheck(title="unknown config keys", run=_unknown_keys)
TOKEN_STORE = DoctorCheck(title="pass token store", run=_token_store)


def _other_readers() -> str:
    """`` (another untaped here may read them: …)`` when other installs are registered."""
    from untaped.sdk.storage._refs import others_text  # noqa: PLC0415 - reads refs/ only here

    others = others_text()
    return f" (another untaped here may read them: {others})" if others else ""


def _collect_unknown(
    node: Mapping[str, Any],
    location: tuple[str, ...],
    path: tuple[str, ...],
    leaves: set[tuple[str, ...]],
    prefixes: set[tuple[str, ...]],
    unknown: list[str],
) -> None:
    for key, value in node.items():
        child = (*path, str(key))
        if child in leaves:
            continue
        if child in prefixes:
            if isinstance(value, dict):
                _collect_unknown(value, location, child, leaves, prefixes, unknown)
            continue
        unknown.append(".".join((*location, *child)))
