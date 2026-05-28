"""Plugin registration, discovery, diagnostics, and uv-backed install commands."""

from __future__ import annotations

import re
import shlex
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import PurePosixPath
from typing import Protocol
from urllib.parse import unquote, urlparse

import typer
from pydantic import BaseModel, ValidationError

from untaped.cli import ColumnsOption, FormatOption, report_errors
from untaped.config_file import mutate_config, read_config_dict
from untaped.errors import ConfigError, first_validation_error
from untaped.output import Row, format_output
from untaped.settings import (
    PluginInstallSpec,
    PluginsState,
    PluginToolSpec,
    register_profile_settings,
    register_state_settings,
    validate_disjoint_settings_sections,
)

ENTRY_POINT_GROUP = "untaped.plugins"
_PACKAGE_NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_NAMED_DIRECT_REFERENCE_RE = re.compile(
    rf"^\s*(?P<name>{_PACKAGE_NAME})(?:\[[^\]]+\])?\s*@\s*(?P<target>.+?)\s*$"
)
_REQUIREMENT_NAME_RE = re.compile(
    rf"^\s*(?P<name>{_PACKAGE_NAME})(?:\[[^\]]+\])?(?=\s*(?:$|[<>=!~;]))"
)
_VCS_PREFIX_RE = re.compile(r"^(?:git|hg|svn|bzr)\+")
_DIRECT_REFERENCE_PREFIXES = ("git+", "hg+", "svn+", "bzr+", "file:")
_ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz", ".whl")


class UntapedPlugin(Protocol):
    """Object exposed by plugin packages through the ``untaped.plugins`` entry point."""

    id: str

    def register(self, registry: PluginRegistry) -> None: ...


@dataclass(frozen=True)
class DiagnosticResult:
    """One plugin diagnostic outcome."""

    name: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class PluginLoadError:
    """A plugin entry point that failed to load or register."""

    name: str
    error: str


class PluginRegistry:
    """In-process registry populated by installed plugins."""

    def __init__(self, *, reserved_cli_names: Iterable[str] = ()) -> None:
        self.reserved_cli_names = set(reserved_cli_names)
        self.plugin_ids: set[str] = set()
        self.clis: dict[str, typer.Typer] = {}
        self.profile_sections: dict[str, type[BaseModel]] = {}
        self.state_sections: dict[str, type[BaseModel]] = {}
        self.diagnostics: dict[str, Callable[[], DiagnosticResult]] = {}
        self.load_errors: list[PluginLoadError] = []

    def add_plugin_id(self, plugin_id: str) -> None:
        if plugin_id in self.plugin_ids:
            raise ConfigError(f"duplicate plugin id: {plugin_id}")
        self.plugin_ids.add(plugin_id)

    def add_cli(self, name: str, app: typer.Typer) -> None:
        if name in self.reserved_cli_names:
            raise ConfigError(f"reserved CLI command: {name}")
        if name in self.clis:
            raise ConfigError(f"duplicate CLI command: {name}")
        self.clis[name] = app

    def add_profile_settings(self, section: str, model: type[BaseModel]) -> None:
        if section in self.profile_sections:
            raise ConfigError(f"duplicate profile settings section: {section}")
        state_model = self.state_sections.get(section)
        if state_model is not None:
            validate_disjoint_settings_sections(section, model, state_model)
        self.profile_sections[section] = model

    def add_state_settings(self, section: str, model: type[BaseModel]) -> None:
        if section in self.state_sections:
            raise ConfigError(f"duplicate state settings section: {section}")
        profile_model = self.profile_sections.get(section)
        if profile_model is not None:
            validate_disjoint_settings_sections(section, profile_model, model)
        self.state_sections[section] = model

    def add_diagnostic(self, name: str, check: Callable[[], DiagnosticResult]) -> None:
        if name in self.diagnostics:
            raise ConfigError(f"duplicate diagnostic: {name}")
        self.diagnostics[name] = check

    def record_load_error(self, name: str, exc: BaseException) -> None:
        self.load_errors.append(PluginLoadError(name=name, error=str(exc)))

    def run_diagnostics(self) -> list[DiagnosticResult]:
        return [check() for check in self.diagnostics.values()]

    def apply_config_sections(self) -> None:
        """Publish successfully registered config sections to the settings registry."""
        for section, model in self.profile_sections.items():
            register_profile_settings(section, model)
        for section, model in self.state_sections.items():
            register_state_settings(section, model)


_CURRENT_REGISTRY = PluginRegistry()


def set_current_registry(registry: PluginRegistry) -> None:
    """Set the registry used by ``untaped plugins`` commands."""
    global _CURRENT_REGISTRY
    _CURRENT_REGISTRY = registry


def discover_plugins(registry: PluginRegistry | None = None) -> list[UntapedPlugin]:
    """Load plugin objects from installed Python entry points."""
    plugins: list[UntapedPlugin] = []
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            plugins.append(ep.load())
        except Exception as exc:
            if registry is None:
                raise
            registry.record_load_error(ep.name, exc)
    return plugins


def register_plugins(registry: PluginRegistry, plugins: Iterable[UntapedPlugin]) -> PluginRegistry:
    """Register plugins, recording failures instead of poisoning the CLI."""
    for plugin in plugins:
        plugin_id = getattr(plugin, "id", plugin.__class__.__module__)
        clis = dict(registry.clis)
        plugin_ids = set(registry.plugin_ids)
        profile_sections = dict(registry.profile_sections)
        state_sections = dict(registry.state_sections)
        diagnostics = dict(registry.diagnostics)
        try:
            registry.add_plugin_id(plugin_id)
            plugin.register(registry)
        except Exception as exc:
            registry.clis = clis
            registry.plugin_ids = plugin_ids
            registry.profile_sections = profile_sections
            registry.state_sections = state_sections
            registry.diagnostics = diagnostics
            registry.record_load_error(plugin_id, exc)
    registry.apply_config_sections()
    return registry


app = typer.Typer(
    name="plugins",
    help="Manage untaped plugins installed into the uv tool environment.",
    no_args_is_help=True,
)


@app.callback()
def _callback() -> None:
    """Manage untaped plugins."""


@app.command("add", no_args_is_help=True)
def add_command(
    package_spec: str = typer.Argument(..., help="uv-compatible plugin package spec."),
    editable: bool = typer.Option(False, "--editable", help="Install with uv --with-editable."),
    no_sync: bool = typer.Option(False, "--no-sync", help="Record only; do not run uv."),
    tool_spec: str | None = typer.Option(
        None,
        "--tool-spec",
        help="uv-compatible untaped tool spec to install instead of recorded/default spec.",
    ),
    editable_tool: bool = typer.Option(False, "--editable-tool", help="Install tool editable."),
) -> None:
    """Record a desired plugin package and optionally rebuild the uv tool env."""
    with report_errors():
        spec = PluginInstallSpec(
            spec=_canonical_plugin_spec(package_spec, reject_uninferable_direct=True),
            editable=editable,
        )
        tool = _tool_override(tool_spec, editable_tool)

        def _apply(data: dict[str, object]) -> None:
            state = _plugin_state_from_config(data)
            updated = _upsert_plugin_spec(state, spec)
            if tool is not None:
                updated = _set_tool_spec(updated, tool)
            if not no_sync:
                _sync_state(updated)
            data["plugins"] = updated.model_dump()

        mutate_config(_apply)
        typer.echo(f"added plugin package: {spec.spec}", err=True)
        if not no_sync:
            typer.echo("plugin environment synced; run a fresh untaped invocation", err=True)


@app.command("remove", no_args_is_help=True)
def remove_command(
    package_spec: str = typer.Argument(..., help="Plugin package spec to remove."),
    no_sync: bool = typer.Option(False, "--no-sync", help="Record only; do not run uv."),
    tool_spec: str | None = typer.Option(
        None,
        "--tool-spec",
        help="uv-compatible untaped tool spec to install instead of recorded/default spec.",
    ),
    editable_tool: bool = typer.Option(False, "--editable-tool", help="Install tool editable."),
) -> None:
    """Remove a desired plugin package and optionally rebuild the uv tool env."""
    with report_errors():
        tool = _tool_override(tool_spec, editable_tool)

        def _apply(data: dict[str, object]) -> None:
            state = _plugin_state_from_config(data)
            updated, removed = _remove_plugin_spec(state, package_spec)
            if not removed:
                raise ConfigError(f"plugin package is not recorded: {package_spec}")
            if tool is not None:
                updated = _set_tool_spec(updated, tool)
            if not no_sync:
                updated = _canonical_plugin_state(updated)
                _sync_state(updated)
            data["plugins"] = updated.model_dump()

        mutate_config(_apply)
        typer.echo(f"removed plugin package: {package_spec}", err=True)
        if not no_sync:
            typer.echo("plugin environment synced; run a fresh untaped invocation", err=True)


@app.command("sync")
def sync_command(
    tool_spec: str | None = typer.Option(
        None,
        "--tool-spec",
        help="uv-compatible untaped tool spec to install instead of recorded/default spec.",
    ),
    editable_tool: bool = typer.Option(False, "--editable-tool", help="Install tool editable."),
) -> None:
    """Rebuild the uv tool environment with every recorded plugin package."""
    with report_errors():
        tool = _tool_override(tool_spec, editable_tool)

        def _apply(data: dict[str, object]) -> None:
            state = _canonical_plugin_state(_plugin_state_from_config(data))
            updated = _set_tool_spec(state, tool) if tool is not None else state
            _sync_state(updated)
            data["plugins"] = updated.model_dump()

        mutate_config(_apply)
        typer.echo("plugin environment synced; run a fresh untaped invocation", err=True)


@app.command("list")
def list_command(
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List loaded plugins and desired plugin packages."""
    with report_errors():
        state = _plugin_state()
        rows = _plugin_rows(state)
        if fmt == "raw" and columns is None:
            rows = [row for row in rows if row["spec"]]
        rendered = format_output(rows, fmt=fmt, columns=columns)
        if rendered:
            typer.echo(rendered)


@app.command("doctor")
def doctor_command() -> None:
    """Report plugin load failures and registered diagnostics."""
    with report_errors():
        failed = False
        for error in _CURRENT_REGISTRY.load_errors:
            failed = True
            typer.echo(f"load-error\t{error.name}\t{error.error}")
        for result in _CURRENT_REGISTRY.run_diagnostics():
            if result.status != "ok":
                failed = True
            parts = [result.status, result.name]
            if result.detail:
                parts.append(result.detail)
            typer.echo("\t".join(parts))
        if failed:
            raise typer.Exit(1)


def _plugin_state() -> PluginsState:
    return _plugin_state_from_config(read_config_dict())


def _plugin_state_from_config(data: Mapping[str, object]) -> PluginsState:
    raw = data.get("plugins") or {}
    if not isinstance(raw, dict):
        return PluginsState()
    try:
        state = PluginsState.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid plugins config: {first_validation_error(exc)}") from exc
    _validate_unique_plugin_specs(state)
    return state


def _validate_unique_plugin_specs(state: PluginsState) -> None:
    seen: set[str] = set()
    for package in state.packages:
        key = _plugin_spec_key(package.spec, reject_bare_direct=False)
        if key in seen:
            raise ConfigError(f"duplicate plugin package spec: {key}")
        seen.add(key)


def _upsert_plugin_spec(state: PluginsState, spec: PluginInstallSpec) -> PluginsState:
    key = _plugin_spec_key(spec.spec, reject_bare_direct=True)
    kept = [p for p in state.packages if _plugin_spec_key(p.spec, reject_bare_direct=False) != key]
    return state.model_copy(update={"packages": [*kept, spec]})


def _remove_plugin_spec(state: PluginsState, package_spec: str) -> tuple[PluginsState, bool]:
    key = _plugin_spec_key(package_spec, reject_bare_direct=False)
    kept = [
        p
        for p in state.packages
        if p.spec != package_spec and _plugin_spec_key(p.spec, reject_bare_direct=False) != key
    ]
    return state.model_copy(update={"packages": kept}), len(kept) != len(state.packages)


def _set_tool_spec(state: PluginsState, tool: PluginToolSpec) -> PluginsState:
    return state.model_copy(update={"tool": tool})


def _canonical_plugin_state(state: PluginsState) -> PluginsState:
    packages = [
        package.model_copy(
            update={
                "spec": _canonical_plugin_spec(
                    package.spec,
                    reject_uninferable_direct=True,
                )
            }
        )
        for package in state.packages
    ]
    return state.model_copy(update={"packages": packages})


def _canonical_plugin_spec(spec: str, *, reject_uninferable_direct: bool) -> str:
    stripped = _stripped_plugin_spec(spec)
    named_direct = _NAMED_DIRECT_REFERENCE_RE.match(stripped)
    if named_direct is not None:
        name = _normalize_package_name(named_direct.group("name"))
        return f"{name} @ {named_direct.group('target').strip()}"
    if _looks_like_direct_reference(stripped):
        inferred = _infer_direct_reference_name(stripped)
        if inferred is not None:
            return f"{inferred} @ {stripped}"
        if reject_uninferable_direct:
            raise _uninferable_direct_reference_error(stripped)
    return stripped


def _tool_override(tool_spec: str | None, editable_tool: bool) -> PluginToolSpec | None:
    if tool_spec is None:
        if editable_tool:
            raise ConfigError("--editable-tool requires --tool-spec")
        return None
    return PluginToolSpec(spec=tool_spec, editable=editable_tool)


def _plugin_spec_key(spec: str, *, reject_bare_direct: bool) -> str:
    stripped = _stripped_plugin_spec(spec)
    named_direct = _NAMED_DIRECT_REFERENCE_RE.match(stripped)
    if named_direct is not None:
        return _normalize_package_name(named_direct.group("name"))
    requirement = _REQUIREMENT_NAME_RE.match(stripped)
    if requirement is not None:
        return _normalize_package_name(requirement.group("name"))
    if _looks_like_direct_reference(stripped):
        inferred = _infer_direct_reference_name(stripped)
        if inferred is not None:
            return inferred
        if reject_bare_direct:
            raise _uninferable_direct_reference_error(stripped)
        return stripped
    return stripped


def _stripped_plugin_spec(spec: str) -> str:
    stripped = spec.strip()
    if not stripped:
        raise ConfigError("plugin package spec cannot be empty")
    return stripped


def _normalize_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _looks_like_direct_reference(spec: str) -> bool:
    return "://" in spec or spec.startswith(_DIRECT_REFERENCE_PREFIXES)


def _infer_direct_reference_name(spec: str) -> str | None:
    if spec.startswith("file:"):
        return None
    target = _VCS_PREFIX_RE.sub("", spec, count=1)
    parsed = urlparse(target)
    path = parsed.path if parsed.scheme else target
    basename = PurePosixPath(unquote(path).rstrip("/")).name
    lowered = basename.lower()
    if lowered.endswith(_ARCHIVE_SUFFIXES):
        return None
    git_ref_index = lowered.find(".git@")
    if git_ref_index != -1:
        basename = basename[: git_ref_index + len(".git")]
        lowered = basename.lower()
    if lowered.endswith(".git"):
        basename = basename[:-4]
    if not basename or re.fullmatch(_PACKAGE_NAME, basename) is None:
        return None
    return _normalize_package_name(basename)


def _uninferable_direct_reference_error(spec: str) -> ConfigError:
    return ConfigError(
        "could not infer plugin name from direct URL; use 'name @ url' "
        f"(for example: untaped-awx @ {spec})"
    )


def _plugin_rows(state: PluginsState) -> list[Row]:
    loaded_ids = set(_CURRENT_REGISTRY.plugin_ids)
    matched_loaded_ids: set[str] = set()
    rows: dict[str, Row] = {}

    for package in state.packages:
        package_name = _plugin_spec_key(package.spec, reject_bare_direct=False)
        plugin_id = _matched_loaded_plugin_id(package_name, loaded_ids)
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


def _matched_loaded_plugin_id(package_name: str, loaded_ids: set[str]) -> str | None:
    normalized_loaded_ids = {
        _normalize_package_name(plugin_id): plugin_id for plugin_id in loaded_ids
    }
    direct = normalized_loaded_ids.get(package_name)
    if direct is not None:
        return direct
    if package_name.startswith("untaped-"):
        return normalized_loaded_ids.get(package_name.removeprefix("untaped-"))
    return None


def _sync_state(state: PluginsState) -> None:
    _validate_syncable_plugins(state)
    cmd = _uv_tool_install_command(state.tool, state.packages)
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        rendered = " ".join(shlex.quote(part) for part in cmd)
        raise ConfigError(f"plugin sync failed with exit {result.returncode}: {rendered}")


def _validate_syncable_plugins(state: PluginsState) -> None:
    for package in state.packages:
        _plugin_spec_key(package.spec, reject_bare_direct=True)


def _uv_tool_install_command(tool: PluginToolSpec, packages: list[PluginInstallSpec]) -> list[str]:
    cmd = ["uv", "tool", "install", tool.spec]
    if tool.editable:
        cmd.append("--editable")
    # Plugin repos may carry `tool.uv.sources` for their own development.
    # The installed tool env should resolve only from the explicit tool/plugin
    # specs recorded in untaped state, otherwise editable core installs can
    # conflict with a plugin's dev-only source pin back to core.
    cmd.append("--no-sources")
    for package in packages:
        cmd.extend(["--with-editable" if package.editable else "--with", package.spec])
    # `uv tool install` refuses an already installed tool without --force; sync
    # intentionally rebuilds the existing untaped tool env with the recorded set.
    cmd.append("--force")
    return cmd
