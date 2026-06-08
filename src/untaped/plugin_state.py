"""Recorded plugin state helpers."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError

from untaped.config_file import read_config_dict
from untaped.errors import ConfigError, first_validation_error
from untaped.output import Row
from untaped.plugin_specs import canonical_plugin_spec, normalize_package_name, plugin_spec_key
from untaped.settings import PluginInstallSpec, PluginsState, PluginToolSpec


def plugin_state() -> PluginsState:
    """Read recorded plugin state from config."""
    return plugin_state_from_config(read_config_dict())


def plugin_state_from_config(data: Mapping[str, object]) -> PluginsState:
    """Parse and validate recorded plugin state from raw config data."""
    raw = data.get("plugins") or {}
    if not isinstance(raw, dict):
        return PluginsState()
    try:
        state = PluginsState.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid plugins config: {first_validation_error(exc)}") from exc
    validate_unique_plugin_specs(state)
    return state


def validate_unique_plugin_specs(state: PluginsState) -> None:
    """Reject duplicate recorded plugin package identities."""
    seen: set[str] = set()
    for package in state.packages:
        key = plugin_package_key(package)
        if key in seen:
            raise ConfigError(f"duplicate plugin package spec: {key}")
        seen.add(key)


def upsert_plugin_spec(state: PluginsState, spec: PluginInstallSpec) -> PluginsState:
    """Return state with ``spec`` inserted or replacing the matching package."""
    key = plugin_package_key(spec, reject_bare_direct=True)
    kept = [p for p in state.packages if plugin_package_key(p) != key]
    return state.model_copy(update={"packages": [*kept, spec]})


def remove_plugin_spec(state: PluginsState, package_spec: str) -> tuple[PluginsState, bool]:
    """Return state with ``package_spec`` removed plus whether anything changed."""
    key = plugin_spec_key(package_spec, reject_bare_direct=False)
    kept = [p for p in state.packages if p.spec != package_spec and plugin_package_key(p) != key]
    return state.model_copy(update={"packages": kept}), len(kept) != len(state.packages)


def set_tool_spec(state: PluginsState, tool: PluginToolSpec) -> PluginsState:
    """Return state with an updated core install spec."""
    return state.model_copy(update={"tool": tool})


def dump_plugin_state(state: PluginsState) -> dict[str, object]:
    """Serialize plugin state without writing an unrecorded core spec."""
    data: dict[str, object] = {}
    if state.packages:
        data["packages"] = [package.model_dump(exclude_none=True) for package in state.packages]
    if state.tool.spec is not None:
        data["tool"] = state.tool.model_dump()
    return data


def canonical_plugin_state(state: PluginsState) -> PluginsState:
    """Canonicalize every recorded plugin package spec."""
    packages = [
        package.model_copy(
            update={
                "spec": canonical_plugin_spec(
                    package.spec,
                    reject_uninferable_direct=True,
                )
            }
        )
        for package in state.packages
    ]
    return state.model_copy(update={"packages": packages})


def plugin_rows(state: PluginsState, loaded_ids: set[str]) -> list[Row]:
    """Render loaded/recorded plugin state rows."""
    matched_loaded_ids: set[str] = set()
    rows: dict[str, Row] = {}

    for package in state.packages:
        package_name = plugin_package_key(package)
        plugin_id = matched_loaded_plugin_id(package_name, loaded_ids)
        if plugin_id is not None:
            matched_loaded_ids.add(plugin_id)
        rows[package_name] = {
            "name": package_name,
            "status": "installed" if plugin_id is not None else "recorded",
            "plugin_id": plugin_id or "",
            "editable": package.editable,
            "spec": package.spec,
        }

    for plugin_id in sorted(loaded_ids - matched_loaded_ids):
        rows[plugin_id] = {
            "name": plugin_id,
            "status": "loaded",
            "plugin_id": plugin_id,
            "editable": None,
            "spec": "",
        }
    return [rows[name] for name in sorted(rows)]


def matched_loaded_plugin_id(package_name: str, loaded_ids: set[str]) -> str | None:
    """Return the loaded plugin id corresponding to a recorded package name."""
    normalized_loaded_ids = {
        normalize_package_name(plugin_id): plugin_id for plugin_id in loaded_ids
    }
    direct = normalized_loaded_ids.get(package_name)
    if direct is not None:
        return direct
    if package_name.startswith("untaped-"):
        return normalized_loaded_ids.get(package_name.removeprefix("untaped-"))
    return None


def plugin_package_key(
    package: PluginInstallSpec,
    *,
    reject_bare_direct: bool = False,
) -> str:
    """Return the stable package identity for a recorded install spec."""
    if package.name:
        return normalize_package_name(package.name)
    return plugin_spec_key(package.spec, reject_bare_direct=reject_bare_direct)
