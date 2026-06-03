"""uv-backed plugin environment sync helpers."""

from __future__ import annotations

import shlex
import subprocess

from untaped.errors import ConfigError
from untaped.plugin_specs import plugin_spec_key
from untaped.settings import PluginInstallSpec, PluginsState, PluginToolSpec


def sync_state(state: PluginsState) -> None:
    """Rebuild the uv tool environment for recorded plugin state."""
    validate_syncable_plugins(state)
    cmd = uv_tool_install_command(state.tool, state.packages)
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        rendered = " ".join(shlex.quote(part) for part in cmd)
        raise ConfigError(f"plugin sync failed with exit {result.returncode}: {rendered}")


def validate_syncable_plugins(state: PluginsState) -> None:
    """Ensure every recorded plugin package can be addressed by a stable key."""
    for package in state.packages:
        plugin_spec_key(package.spec, reject_bare_direct=True)


def uv_tool_install_command(tool: PluginToolSpec, packages: list[PluginInstallSpec]) -> list[str]:
    """Build the `uv tool install` command for the recorded plugin environment."""
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
