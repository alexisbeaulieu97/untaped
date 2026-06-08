"""Installer helpers for the managed untaped virtual environment."""

from __future__ import annotations

import argparse
import shlex
import stat
import subprocess
from pathlib import Path

from untaped.config_file import mutate_config
from untaped.install_paths import default_managed_venv_path, default_shim_path
from untaped.plugin_state import (
    dump_plugin_state,
    plugin_state,
    plugin_state_from_config,
    set_tool_spec,
)
from untaped.plugin_sync import (
    explicit_package_names,
    managed_env_lock,
    render_requirements,
    uv_pip_compile_command,
    uv_pip_sync_command,
    uv_venv_command,
    venv_python,
)
from untaped.settings import PluginsState, PluginToolSpec


def record_core_install(spec: str, *, editable: bool = False) -> None:
    """Persist the core package spec used to sync the managed environment."""

    def _apply(data: dict[str, object]) -> None:
        state = plugin_state_from_config(data)
        updated = set_tool_spec(state, PluginToolSpec(spec=spec, editable=editable))
        data["plugins"] = dump_plugin_state(updated)

    mutate_config(_apply)


def write_shim(venv: Path, shim: Path) -> None:
    """Write a small executable that delegates to the managed venv binary."""
    shim.parent.mkdir(parents=True, exist_ok=True)
    target = venv / "bin" / "untaped"
    shim.write_text(f'#!/usr/bin/env sh\nexec {shlex.quote(str(target))} "$@"\n')
    mode = shim.stat().st_mode
    shim.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def bootstrap_core_install(
    spec: str,
    *,
    editable: bool,
    venv: Path,
    shim: Path,
    requirements: Path,
    resolved: Path,
) -> None:
    """Install core into the managed venv under the shared environment lock."""
    with managed_env_lock(venv):
        state = set_tool_spec(
            plugin_state(),
            PluginToolSpec(spec=spec, editable=editable),
        )
        requirements.write_text(
            render_requirements(state.tool, state.packages),
            encoding="utf-8",
        )
        python = venv_python(venv)
        if not python.exists():
            _run_command(uv_venv_command(venv), "managed venv creation failed")
        _run_command(
            uv_pip_compile_command(
                python,
                requirements,
                resolved,
                no_sources_packages=explicit_package_names(state),
            ),
            "core dependency resolution failed",
        )
        _run_command(uv_pip_sync_command(python, resolved), "core install failed")
        persist_core_install_state(state)
        write_shim(venv, shim)


def persist_core_install_state(state: PluginsState) -> None:
    def _apply(data: dict[str, object]) -> None:
        current = plugin_state_from_config(data)
        updated = set_tool_spec(current, state.tool)
        data["plugins"] = dump_plugin_state(updated)

    mutate_config(_apply)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", help="uv-compatible untaped core package spec")
    parser.add_argument("--editable", action="store_true", help="Record the core spec as editable")
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Create/sync the managed venv before recording the core spec",
    )
    parser.add_argument(
        "--requirements",
        type=Path,
        help="Top-level core requirements file for --sync",
    )
    parser.add_argument(
        "--resolved",
        type=Path,
        help="Resolved requirements output file for --sync",
    )
    parser.add_argument(
        "--venv",
        type=Path,
        default=default_managed_venv_path(),
        help="Managed virtual environment path",
    )
    parser.add_argument(
        "--shim",
        type=Path,
        default=default_shim_path(),
        help="Shim path to write",
    )
    args = parser.parse_args(argv)

    if args.sync:
        if args.requirements is None or args.resolved is None:
            parser.error("--sync requires --requirements and --resolved")
        bootstrap_core_install(
            args.spec,
            editable=args.editable,
            venv=args.venv,
            shim=args.shim,
            requirements=args.requirements,
            resolved=args.resolved,
        )
    else:
        record_core_install(args.spec, editable=args.editable)
        write_shim(args.venv, args.shim)


def _run_command(cmd: list[str], failure: str) -> None:
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        rendered = " ".join(shlex.quote(part) for part in cmd)
        raise SystemExit(f"{failure} with exit {result.returncode}: {rendered}")


if __name__ == "__main__":
    main()
