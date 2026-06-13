"""Plugin Cyclopts commands and public plugin runtime facade."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from untaped.cli import (
    ColumnsOption,
    FormatOption,
    create_app,
    echo,
    raise_usage,
    render_rows,
    report_errors,
)
from untaped.config_file import mutate_config
from untaped.errors import ConfigError
from untaped.plugin_deps import expand_plugin_dependencies
from untaped.plugin_registry import (
    ENTRY_POINT_GROUP,
    CliSpec,
    DiagnosticResult,
    PluginLoadError,
    PluginManifest,
    PluginRegistry,
    RootOptionSpec,
    SettingsLayoutSpec,
    SkillSpec,
    UntapedPlugin,
    current_registry,
    discover_plugins,
    register_plugins,
    set_current_registry,
)
from untaped.plugin_specs import (
    canonical_plugin_spec,
    looks_like_direct_reference,
    normalize_package_name,
    plugin_spec_key,
    unique_plugin_specs,
)
from untaped.plugin_state import (
    canonical_plugin_state,
    dump_plugin_state,
    plugin_package_key,
    plugin_rows,
    plugin_state,
    plugin_state_from_config,
    remove_plugin_spec,
    upsert_plugin_spec,
)
from untaped.plugin_sync import managed_env_lock, sync_state_unlocked
from untaped.settings import PluginInstallSpec, PluginsState
from untaped.stdin import read_identifiers
from untaped.ui import ui_context

__all__ = [
    "ENTRY_POINT_GROUP",
    "CliSpec",
    "DiagnosticResult",
    "PluginLoadError",
    "PluginManifest",
    "PluginRegistry",
    "RootOptionSpec",
    "SettingsLayoutSpec",
    "SkillSpec",
    "UntapedPlugin",
    "app",
    "current_registry",
    "discover_plugins",
    "register_plugins",
    "set_current_registry",
]

app = create_app(
    name="plugins",
    help="Manage untaped plugins installed into the managed virtual environment.",
)


@app.command(name="add")
def add_command(
    package_specs: Annotated[
        list[str] | None,
        Parameter(help="uv-compatible plugin package spec(s)."),
    ] = None,
    *,
    stdin: Annotated[
        bool,
        Parameter(name="--stdin", help="Read package specs from stdin."),
    ] = False,
    editable: Annotated[
        bool,
        Parameter(name="--editable", help="Install package spec editable."),
    ] = False,
    no_sync: Annotated[
        bool,
        Parameter(name="--no-sync", help="Record only; do not run uv."),
    ] = False,
    no_auto_deps: Annotated[
        bool,
        Parameter(
            name="--no-auto-deps",
            help="Do not auto-record local plugin dependencies.",
        ),
    ] = False,
) -> None:
    """Record desired plugin packages and optionally sync the managed venv."""
    if not package_specs and not stdin:
        raise_usage("provide package spec(s) or --stdin")
    with report_errors():
        requested_specs = read_identifiers(list(package_specs or []), stdin=stdin)
        specs = [
            canonical_install_spec(package_spec, editable=editable)
            for package_spec in requested_specs
        ]
        auto_specs: list[tuple[PluginInstallSpec, str]] = []
        if not no_auto_deps:
            recorded = {plugin_package_key(package) for package in plugin_state().packages}
            auto_specs = expand_plugin_dependencies(specs, already_recorded=recorded)

        record_added_specs(specs + [spec for spec, _ in auto_specs], sync=not no_sync)
        ui = ui_context(strict=False)
        for spec, parent in auto_specs:
            ui.message(
                "info",
                f"auto-recorded plugin dependency: {spec.spec} (required by {parent})",
            )
        for spec in specs:
            ui.message("success", f"added plugin package: {spec.spec}")
        if not no_sync:
            ui.message("info", "plugin environment synced; run a fresh untaped invocation")


@app.command(name="remove")
def remove_command(
    package_specs: Annotated[
        list[str] | None,
        Parameter(help="Plugin package spec(s) to remove."),
    ] = None,
    *,
    stdin: Annotated[
        bool,
        Parameter(name="--stdin", help="Read package specs from stdin."),
    ] = False,
    no_sync: Annotated[
        bool,
        Parameter(name="--no-sync", help="Record only; do not run uv."),
    ] = False,
) -> None:
    """Remove desired plugin packages and optionally sync the managed venv."""
    if not package_specs and not stdin:
        raise_usage("provide package spec(s) or --stdin")
    with report_errors():
        requested_specs = unique_plugin_specs(
            read_identifiers(list(package_specs or []), stdin=stdin)
        )

        record_removed_specs(requested_specs, sync=not no_sync)
        ui = ui_context(strict=False)
        for package_spec in requested_specs:
            ui.message("success", f"removed plugin package: {package_spec}")
        if not no_sync:
            ui.message("info", "plugin environment synced; run a fresh untaped invocation")


@app.command(name="sync")
def sync_command() -> None:
    """Rebuild the managed venv with every recorded plugin package."""
    with report_errors():
        with managed_env_lock():
            original = plugin_state()
            state = canonical_plugin_state(original)
            sync_state_unlocked(state)

            def _apply(data: dict[str, object]) -> None:
                if plugin_state_from_config(data) == original:
                    store_plugin_state(data, state)

            mutate_config(_apply)
        ui = ui_context(strict=False)
        ui.message("info", "plugin environment synced; run a fresh untaped invocation")


@app.command(name="list")
def list_command(
    *,
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
        rendered = render_rows(rows, fmt=fmt, columns=columns)
        if rendered:
            echo(rendered)


@app.command(name="doctor")
def doctor_command() -> None:
    """Report plugin load failures and registered diagnostics."""
    with report_errors():
        failed = False
        registry = current_registry()
        for error in registry.load_errors:
            failed = True
            echo(f"load-error\t{error.name}\t{error.error}")
        for result in registry.run_diagnostics():
            if result.status != "ok":
                failed = True
            parts = [result.status, result.name]
            if result.detail:
                parts.append(result.detail)
            echo("\t".join(parts))
        if failed:
            raise SystemExit(1)


def canonical_install_spec(package_spec: str, *, editable: bool) -> PluginInstallSpec:
    """Return the recorded install spec with stable local-path identity."""
    canonical = canonical_plugin_spec(
        package_spec,
        reject_uninferable_direct=True,
    )
    if plugin_spec_key(canonical, reject_bare_direct=False) == canonical:
        local_path = local_install_path(canonical)
        if local_path is not None:
            return PluginInstallSpec(
                spec=str(local_path),
                editable=editable,
                name=read_local_package_name(local_path),
            )
    return PluginInstallSpec(
        spec=canonical,
        editable=editable,
    )


def local_install_path(package_spec: str) -> Path | None:
    """Resolve local path specs while leaving package requirements alone."""
    if looks_like_direct_reference(package_spec):
        return None
    path = Path(package_spec).expanduser()
    if path.exists():
        return path.resolve()
    if looks_like_path_spec(package_spec):
        raise ConfigError(f"editable plugin path does not exist: {package_spec}")
    return None


def read_local_package_name(path: Path) -> str:
    """Read or infer the normalized package name from a local install path."""
    if path.is_dir():
        return read_project_name(path)
    if path.suffix == ".whl":
        return read_wheel_name(path)
    raise ConfigError(
        f"could not infer plugin name from local path: {path}; use 'name @ file://...' "
        "or a local project directory"
    )


def read_wheel_name(path: Path) -> str:
    """Infer a package name from a local wheel filename."""
    name = path.name.split("-", 1)[0]
    if not name:
        raise ConfigError(f"could not infer plugin name from wheel path: {path}")
    return normalize_package_name(name)


def looks_like_path_spec(package_spec: str) -> bool:
    return (
        package_spec.startswith((".", "~"))
        or package_spec.startswith(os.sep)
        or os.sep in package_spec
    )


def read_project_name(path: Path) -> str:
    """Read the normalized package name from a local project's pyproject.toml."""
    pyproject = path / "pyproject.toml"
    if not pyproject.is_file():
        raise ConfigError(f"editable plugin path has no pyproject.toml: {path}")
    try:
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"could not parse editable plugin pyproject {pyproject}: {exc}") from exc
    project = data.get("project")
    if not isinstance(project, dict) or not isinstance(project.get("name"), str):
        raise ConfigError(f"editable plugin path has no project.name: {pyproject}")
    return normalize_package_name(project["name"])


def record_added_specs(specs: list[PluginInstallSpec], *, sync: bool) -> None:
    """Persist added package specs, then sync outside the config lock."""
    if not sync:
        mutate_config(lambda data: store_plugin_state(data, add_specs_to_state(data, specs)))
        return

    with managed_env_lock():
        before, updated = mutate_added_specs(specs)
        try:
            sync_state_unlocked(updated)
        except Exception:
            rollback_added_specs(specs, before)
            raise


def record_removed_specs(requested_specs: list[str], *, sync: bool) -> None:
    """Persist removed package specs, then sync outside the config lock."""
    if not sync:
        mutate_config(
            lambda data: store_plugin_state(
                data,
                remove_specs_from_state(
                    plugin_state_from_config(data),
                    requested_specs,
                    canonicalize=False,
                ),
            )
        )
        return

    with managed_env_lock():
        before, updated = mutate_removed_specs(requested_specs)
        try:
            sync_state_unlocked(updated)
        except Exception:
            rollback_removed_specs(requested_specs, before)
            raise


def mutate_added_specs(specs: list[PluginInstallSpec]) -> tuple[PluginsState, PluginsState]:
    before: PluginsState | None = None
    updated: PluginsState | None = None

    def _apply(data: dict[str, object]) -> None:
        nonlocal before, updated
        before = plugin_state_from_config(data)
        updated = add_specs_to_state(data, specs)
        store_plugin_state(data, updated)

    mutate_config(_apply)
    return require_state_update(before, updated)


def mutate_removed_specs(requested_specs: list[str]) -> tuple[PluginsState, PluginsState]:
    before: PluginsState | None = None
    updated: PluginsState | None = None

    def _apply(data: dict[str, object]) -> None:
        nonlocal before, updated
        before = plugin_state_from_config(data)
        updated = remove_specs_from_state(before, requested_specs, canonicalize=True)
        store_plugin_state(data, updated)

    mutate_config(_apply)
    return require_state_update(before, updated)


def add_specs_to_state(
    data: dict[str, object],
    specs: list[PluginInstallSpec],
) -> PluginsState:
    updated = plugin_state_from_config(data)
    for spec in specs:
        updated = upsert_plugin_spec(updated, spec)
    return updated


def remove_specs_from_state(
    state: PluginsState,
    requested_specs: list[str],
    *,
    canonicalize: bool,
) -> PluginsState:
    updated = state
    missing: list[str] = []
    for package_spec in requested_specs:
        updated, removed = remove_plugin_spec(updated, package_spec)
        if not removed:
            missing.append(package_spec)
    raise_for_missing_specs(missing)
    return canonical_plugin_state(updated) if canonicalize else updated


def require_state_update(
    before: PluginsState | None,
    updated: PluginsState | None,
) -> tuple[PluginsState, PluginsState]:
    if before is None or updated is None:
        raise ConfigError("plugin state was not updated")
    return before, updated


def raise_for_missing_specs(missing: list[str]) -> None:
    if not missing:
        return
    if len(missing) == 1:
        raise ConfigError(f"plugin package is not recorded: {missing[0]}")
    raise ConfigError(f"plugin packages are not recorded: {', '.join(missing)}")


def store_plugin_state(data: dict[str, object], state: PluginsState) -> None:
    """Write plugin state, omitting an empty uninitialized section."""
    if state.tool.spec is None and not state.packages:
        data.pop("plugins", None)
    else:
        data["plugins"] = dump_plugin_state(state)


def rollback_added_specs(specs: list[PluginInstallSpec], before: PluginsState) -> None:
    """Undo package additions while preserving unrelated concurrent changes."""
    added_keys = {plugin_package_key(spec) for spec in specs}
    before_by_key = {plugin_package_key(package): package for package in before.packages}

    def _apply(data: dict[str, object]) -> None:
        current = plugin_state_from_config(data)
        packages = [
            package for package in current.packages if plugin_package_key(package) not in added_keys
        ]
        present = {plugin_package_key(package) for package in packages}
        for key, package in before_by_key.items():
            if key in added_keys and key not in present:
                packages.append(package)
                present.add(key)
        store_plugin_state(data, current.model_copy(update={"packages": packages}))

    mutate_config(_apply)


def rollback_removed_specs(requested_specs: list[str], before: PluginsState) -> None:
    """Restore removed packages while preserving unrelated concurrent changes."""
    removed_keys = removed_package_keys(requested_specs, before)
    before_by_key = {plugin_package_key(package): package for package in before.packages}

    def _apply(data: dict[str, object]) -> None:
        current = plugin_state_from_config(data)
        packages = list(current.packages)
        present = {plugin_package_key(package) for package in packages}
        for key, package in before_by_key.items():
            if key in removed_keys and key not in present:
                packages.append(package)
                present.add(key)
        store_plugin_state(data, current.model_copy(update={"packages": packages}))

    mutate_config(_apply)


def _package_key(spec: str) -> str:
    return plugin_spec_key(spec, reject_bare_direct=False)


def removed_package_keys(requested_specs: list[str], before: PluginsState) -> set[str]:
    """Return canonical package keys removed by the requested spec strings."""
    keys: set[str] = set()
    for requested in requested_specs:
        requested_key = _package_key(requested)
        for package in before.packages:
            package_key = plugin_package_key(package)
            if package.spec == requested or package_key == requested_key:
                keys.add(package_key)
    return keys
