"""Plugin Typer commands and public plugin runtime facade."""

from __future__ import annotations

from typing import Annotated

import typer

from untaped.cli import ColumnsOption, FormatOption, report_errors
from untaped.config_file import mutate_config
from untaped.errors import ConfigError
from untaped.output import format_output
from untaped.plugin_registry import (
    ENTRY_POINT_GROUP,
    DiagnosticResult,
    PluginLoadError,
    PluginRegistry,
    UntapedPlugin,
    current_registry,
    discover_plugins,
    register_plugins,
    set_current_registry,
)
from untaped.plugin_specs import canonical_plugin_spec, unique_plugin_specs
from untaped.plugin_state import (
    canonical_plugin_state,
    plugin_rows,
    plugin_state,
    plugin_state_from_config,
    remove_plugin_spec,
    set_tool_spec,
    upsert_plugin_spec,
)
from untaped.plugin_sync import sync_state
from untaped.settings import PluginInstallSpec, PluginToolSpec
from untaped.stdin import read_identifiers

__all__ = [
    "ENTRY_POINT_GROUP",
    "DiagnosticResult",
    "PluginLoadError",
    "PluginRegistry",
    "UntapedPlugin",
    "app",
    "current_registry",
    "discover_plugins",
    "register_plugins",
    "set_current_registry",
]

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
    package_specs: Annotated[
        list[str] | None,
        typer.Argument(help="uv-compatible plugin package spec(s)."),
    ] = None,
    stdin: bool = typer.Option(False, "--stdin", help="Read package specs from stdin."),
    editable: bool = typer.Option(False, "--editable", help="Install with uv --with-editable."),
    no_sync: bool = typer.Option(False, "--no-sync", help="Record only; do not run uv."),
    tool_spec: str | None = typer.Option(
        None,
        "--tool-spec",
        help="uv-compatible untaped tool spec to install instead of recorded/default spec.",
    ),
    editable_tool: bool = typer.Option(False, "--editable-tool", help="Install tool editable."),
) -> None:
    """Record desired plugin packages and optionally rebuild the uv tool env."""
    with report_errors():
        requested_specs = read_identifiers(list(package_specs or []), stdin=stdin)
        specs = [
            PluginInstallSpec(
                spec=canonical_plugin_spec(
                    package_spec,
                    reject_uninferable_direct=True,
                ),
                editable=editable,
            )
            for package_spec in requested_specs
        ]
        tool = _tool_override(tool_spec, editable_tool)

        def _apply(data: dict[str, object]) -> None:
            state = plugin_state_from_config(data)
            updated = state
            for spec in specs:
                updated = upsert_plugin_spec(updated, spec)
            if tool is not None:
                updated = set_tool_spec(updated, tool)
            if not no_sync:
                sync_state(updated)
            data["plugins"] = updated.model_dump()

        mutate_config(_apply)
        for spec in specs:
            typer.echo(f"added plugin package: {spec.spec}", err=True)
        if not no_sync:
            typer.echo("plugin environment synced; run a fresh untaped invocation", err=True)


@app.command("remove", no_args_is_help=True)
def remove_command(
    package_specs: Annotated[
        list[str] | None,
        typer.Argument(help="Plugin package spec(s) to remove."),
    ] = None,
    stdin: bool = typer.Option(False, "--stdin", help="Read package specs from stdin."),
    no_sync: bool = typer.Option(False, "--no-sync", help="Record only; do not run uv."),
    tool_spec: str | None = typer.Option(
        None,
        "--tool-spec",
        help="uv-compatible untaped tool spec to install instead of recorded/default spec.",
    ),
    editable_tool: bool = typer.Option(False, "--editable-tool", help="Install tool editable."),
) -> None:
    """Remove desired plugin packages and optionally rebuild the uv tool env."""
    with report_errors():
        requested_specs = unique_plugin_specs(
            read_identifiers(list(package_specs or []), stdin=stdin)
        )
        tool = _tool_override(tool_spec, editable_tool)

        def _apply(data: dict[str, object]) -> None:
            state = plugin_state_from_config(data)
            updated = state
            missing: list[str] = []
            for package_spec in requested_specs:
                updated, removed = remove_plugin_spec(updated, package_spec)
                if not removed:
                    missing.append(package_spec)
            if missing:
                if len(missing) == 1:
                    raise ConfigError(f"plugin package is not recorded: {missing[0]}")
                raise ConfigError(f"plugin packages are not recorded: {', '.join(missing)}")
            if tool is not None:
                updated = set_tool_spec(updated, tool)
            if not no_sync:
                updated = canonical_plugin_state(updated)
                sync_state(updated)
            data["plugins"] = updated.model_dump()

        mutate_config(_apply)
        for package_spec in requested_specs:
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
            state = canonical_plugin_state(plugin_state_from_config(data))
            updated = set_tool_spec(state, tool) if tool is not None else state
            sync_state(updated)
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
        state = plugin_state()
        registry = current_registry()
        rows = plugin_rows(state, loaded_ids=set(registry.plugin_ids))
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
        registry = current_registry()
        for error in registry.load_errors:
            failed = True
            typer.echo(f"load-error\t{error.name}\t{error.error}")
        for result in registry.run_diagnostics():
            if result.status != "ok":
                failed = True
            parts = [result.status, result.name]
            if result.detail:
                parts.append(result.detail)
            typer.echo("\t".join(parts))
        if failed:
            raise typer.Exit(1)


def _tool_override(tool_spec: str | None, editable_tool: bool) -> PluginToolSpec | None:
    if tool_spec is None:
        if editable_tool:
            raise ConfigError("--editable-tool requires --tool-spec")
        return None
    return PluginToolSpec(spec=tool_spec, editable=editable_tool)
