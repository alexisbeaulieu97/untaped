"""Managed-environment sanity checks (interpreter vs managed venv vs shim).

Plugin discovery only sees packages installed in the running interpreter's
environment, so an ``untaped`` launched from a foreign environment (for
example a leftover ``uv tool install`` shim) silently loads zero plugins.
These checks surface that situation instead of leaving it invisible.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from untaped.errors import ConfigError
from untaped.install_paths import default_managed_venv_path, default_shim_path
from untaped.plugin_registry import DiagnosticResult
from untaped.plugin_state import plugin_state
from untaped.plugin_sync import venv_python
from untaped.settings import PluginsState

_DIAGNOSTIC_NAME = "core-environment"
_FIX_HINT = (
    "run scripts/install.sh to repair the managed install, "
    "or remove the conflicting shim (uv tool uninstall untaped)"
)


def managed_env_mismatch(
    *,
    prefix: Path,
    venv: Path,
    loaded_plugin_count: int,
    state_reader: Callable[[], PluginsState],
) -> str | None:
    """Return a startup warning when recorded plugins cannot load here.

    Ordered so the common healthy paths never touch the config file: the
    state YAML is only read once the interpreter is known to be foreign,
    no plugins loaded, and a managed venv actually exists.
    """
    if _same_path(prefix, venv):
        return None
    if loaded_plugin_count:
        return None
    if not venv_python(venv).exists():
        return None
    try:
        recorded = len(state_reader().packages)
    except ConfigError:
        # The command itself will surface the broken config with a better error.
        return None
    if not recorded:
        return None
    plural = "" if recorded == 1 else "s"
    return (
        f"warning: {recorded} plugin{plural} recorded but this untaped is not "
        f"the managed install ({prefix}); plugin commands will not appear. {_FIX_HINT}"
    )


def startup_mismatch_warning(loaded_plugin_count: int) -> str | None:
    """Production wrapper over :func:`managed_env_mismatch` using real paths."""
    return managed_env_mismatch(
        prefix=Path(sys.prefix),
        venv=default_managed_venv_path(),
        loaded_plugin_count=loaded_plugin_count,
        state_reader=plugin_state,
    )


def environment_check(
    *,
    prefix: Path,
    venv: Path,
    shim: Path,
    state_reader: Callable[[], PluginsState],
) -> DiagnosticResult:
    """Doctor check that interpreter, managed venv, and shim agree."""
    try:
        recorded = len(state_reader().packages)
    except ConfigError as exc:
        return DiagnosticResult(name=_DIAGNOSTIC_NAME, status="error", detail=str(exc))
    if recorded and not _same_path(prefix, venv):
        plural = "" if recorded == 1 else "s"
        return DiagnosticResult(
            name=_DIAGNOSTIC_NAME,
            status="error",
            detail=(
                f"{recorded} plugin{plural} recorded but the running interpreter "
                f"({prefix}) is not the managed venv ({venv}); {_FIX_HINT}"
            ),
        )
    if venv_python(venv).exists() and shim.is_file():
        # A foreign shim only matters once a managed install exists to
        # delegate to; without one, a uv-tool-only setup is legitimate.
        target = str(venv / "bin" / "untaped")
        if target not in shim.read_text(encoding="utf-8"):
            return DiagnosticResult(
                name=_DIAGNOSTIC_NAME,
                status="error",
                detail=(
                    f"shim {shim} does not delegate to the managed venv "
                    f"({target}); re-run scripts/install.sh"
                ),
            )
    return DiagnosticResult(name=_DIAGNOSTIC_NAME, status="ok")


def environment_diagnostic() -> DiagnosticResult:
    """Zero-argument production diagnostic registered with the plugin registry."""
    return environment_check(
        prefix=Path(sys.prefix),
        venv=default_managed_venv_path(),
        shim=default_shim_path(),
        state_reader=plugin_state,
    )


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b
