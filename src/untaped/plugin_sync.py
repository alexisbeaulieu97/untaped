"""uv-backed plugin environment sync helpers."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from filelock import FileLock, Timeout

from untaped.errors import ConfigError
from untaped.install_paths import default_managed_venv_path
from untaped.plugin_specs import plugin_spec_key
from untaped.settings import PluginInstallSpec, PluginsState, PluginToolSpec

_DEFAULT_ENV_LOCK_TIMEOUT = 300.0


class SyncProgress(Protocol):
    """Narrow progress sink for managed-env sync, kept UI-free.

    ``phase`` announces a new step; ``verbose`` selects live tool output over
    output captured and surfaced only on failure. The concrete adapter lives at
    the composition root (``plugins.py``), which owns the ``UiContext``.
    """

    def phase(self, label: str) -> None: ...

    @property
    def verbose(self) -> bool: ...


def sync_state(state: PluginsState, *, progress: SyncProgress | None = None) -> None:
    """Rebuild the managed untaped virtual environment for recorded state."""
    with managed_env_lock(progress=progress):
        sync_state_unlocked(state, progress=progress)


def sync_state_unlocked(state: PluginsState, *, progress: SyncProgress | None = None) -> None:
    """Rebuild the managed untaped virtual environment without locking it."""
    validate_syncable_plugins(state)
    venv = default_managed_venv_path()
    python = venv_python(venv)
    verbose = progress.verbose if progress is not None else False
    if not python.exists():
        _run_command(uv_venv_command(venv), "plugin venv creation failed", verbose=verbose)
    requirements = render_requirements(state.tool, state.packages)
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "requirements.in"
        output_path = Path(tmp) / "requirements.txt"
        input_path.write_text(requirements, encoding="utf-8")
        if progress is not None:
            progress.phase("Resolving plugin dependencies…")
        _run_command(
            uv_pip_compile_command(
                python,
                input_path,
                output_path,
            ),
            "plugin dependency resolution failed",
            hint=(
                "plugin [tool.uv.sources] entries are ignored here; if a plugin "
                "depends on another untaped plugin that is not on an index, record "
                "the dependency explicitly with `untaped plugins add <path-or-url>`"
            ),
            verbose=verbose,
        )
        if progress is not None:
            progress.phase("Installing plugins…")
        _run_command(
            uv_pip_sync_command(python, output_path),
            "plugin sync failed",
            verbose=verbose,
        )


@contextmanager
def managed_env_lock(
    venv: Path | None = None, *, progress: SyncProgress | None = None
) -> Iterator[None]:
    """Serialize writes to the managed untaped virtual environment."""
    venv = venv or default_managed_venv_path()
    lock_path = venv.parent / "venv.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    timeout = float(os.environ.get("UNTAPED_PLUGIN_ENV_LOCK_TIMEOUT", _DEFAULT_ENV_LOCK_TIMEOUT))
    lock = FileLock(str(lock_path), timeout=timeout)
    _acquire_env_lock(lock, venv, timeout, progress)
    try:
        yield
    finally:
        lock.release()


def _acquire_env_lock(
    lock: FileLock, venv: Path, timeout: float, progress: SyncProgress | None
) -> None:
    """Grab the env lock, announcing the wait only when actually contended."""
    try:
        lock.acquire(timeout=0)
        return
    except Timeout:
        pass
    if progress is not None:
        progress.phase("Waiting for another untaped process to finish…")
    try:
        lock.acquire()
    except Timeout as exc:
        raise ConfigError(
            f"could not acquire lock on {venv}; another untaped plugin sync is "
            f"running (waited {timeout}s)."
        ) from exc


def validate_syncable_plugins(state: PluginsState) -> None:
    """Ensure every recorded plugin package can be addressed by a stable key."""
    require_core_spec(state.tool)
    for package in state.packages:
        plugin_spec_key(package.spec, reject_bare_direct=True)


def uv_venv_command(venv: Path) -> list[str]:
    """Build the command that ensures the managed venv exists."""
    return ["uv", "venv", str(venv)]


def uv_pip_sync_command(python: Path, requirements: Path) -> list[str]:
    """Build the command that exact-syncs the managed venv."""
    return ["uv", "pip", "sync", "--python", str(python), "--strict", str(requirements)]


def uv_pip_compile_command(
    python: Path,
    source: Path,
    output: Path,
) -> list[str]:
    """Build the command that resolves top-level specs into exact requirements."""
    return [
        "uv",
        "pip",
        "compile",
        str(source),
        "--output-file",
        str(output),
        "--python",
        str(python),
        "--no-sources",
        "--quiet",
    ]


def venv_python(venv: Path) -> Path:
    """Return the managed venv Python path for the current platform."""
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def render_requirements(tool: PluginToolSpec, packages: list[PluginInstallSpec]) -> str:
    """Render the exact top-level requirements for the managed environment."""
    lines = [_requirement_line(require_core_spec(tool), editable=tool.editable)]
    lines.extend(_requirement_line(package.spec, editable=package.editable) for package in packages)
    return "".join(f"{line}\n" for line in lines)


def require_core_spec(tool: PluginToolSpec) -> str:
    """Return the recorded core spec, or fail with the managed install action."""
    if tool.spec:
        return tool.spec
    raise ConfigError(
        "managed untaped core install spec is not recorded; "
        "install untaped with scripts/install.sh before syncing plugins"
    )


def _requirement_line(spec: str, *, editable: bool) -> str:
    return f"-e {spec}" if editable else spec


def _run_command(
    cmd: list[str], failure: str, *, hint: str | None = None, verbose: bool = False
) -> None:
    returncode, captured = _execute(cmd, verbose=verbose)
    if returncode != 0:
        rendered = " ".join(shlex.quote(part) for part in cmd)
        message = f"{failure} with exit {returncode}: {rendered}"
        if hint is not None:
            message += f"\nhint: {hint}"
        detail = captured.strip()
        if detail:
            message += f"\n{detail}"
        raise ConfigError(message)


def _execute(cmd: list[str], *, verbose: bool) -> tuple[int, str]:
    """Run ``cmd``; under ``verbose`` stream stdio live, else capture for failures."""
    if verbose:
        result = subprocess.run(cmd, check=False)
        return result.returncode, ""
    captured = subprocess.run(cmd, check=False, capture_output=True, text=True)
    return captured.returncode, f"{captured.stdout or ''}{captured.stderr or ''}"
