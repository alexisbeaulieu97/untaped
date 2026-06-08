"""uv-backed plugin environment sync helpers."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from untaped.errors import ConfigError
from untaped.install_paths import default_managed_venv_path
from untaped.plugin_specs import is_package_name, plugin_spec_key
from untaped.plugin_state import plugin_package_key
from untaped.settings import PluginInstallSpec, PluginsState, PluginToolSpec

_DEFAULT_ENV_LOCK_TIMEOUT = 300.0


def sync_state(state: PluginsState) -> None:
    """Rebuild the managed untaped virtual environment for recorded state."""
    with managed_env_lock():
        sync_state_unlocked(state)


def sync_state_unlocked(state: PluginsState) -> None:
    """Rebuild the managed untaped virtual environment without locking it."""
    validate_syncable_plugins(state)
    venv = default_managed_venv_path()
    python = venv_python(venv)
    if not python.exists():
        _run_command(uv_venv_command(venv), "plugin venv creation failed")
    requirements = render_requirements(state.tool, state.packages)
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "requirements.in"
        output_path = Path(tmp) / "requirements.txt"
        input_path.write_text(requirements, encoding="utf-8")
        _run_command(
            uv_pip_compile_command(
                python,
                input_path,
                output_path,
                no_sources_packages=explicit_package_names(state),
            ),
            "plugin dependency resolution failed",
        )
        _run_command(
            uv_pip_sync_command(python, output_path),
            "plugin sync failed",
        )


@contextmanager
def managed_env_lock(venv: Path | None = None) -> Iterator[None]:
    """Serialize writes to the managed untaped virtual environment."""
    venv = venv or default_managed_venv_path()
    lock_path = venv.parent / "venv.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    timeout = float(os.environ.get("UNTAPED_PLUGIN_ENV_LOCK_TIMEOUT", _DEFAULT_ENV_LOCK_TIMEOUT))
    lock = FileLock(str(lock_path), timeout=timeout)
    try:
        lock.acquire()
    except Timeout as exc:
        raise ConfigError(
            f"could not acquire lock on {venv}; another untaped plugin sync is "
            f"running (waited {timeout}s)."
        ) from exc
    try:
        yield
    finally:
        lock.release()


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
    *,
    no_sources_packages: Iterable[str] = ("untaped",),
) -> list[str]:
    """Build the command that resolves top-level specs into exact requirements."""
    command = [
        "uv",
        "pip",
        "compile",
        str(source),
        "--output-file",
        str(output),
        "--python",
        str(python),
    ]
    for package in unique_packages(no_sources_packages):
        command.extend(["--no-sources-package", package])
    command.append("--quiet")
    return command


def explicit_package_names(state: PluginsState) -> list[str]:
    """Return package names whose explicit top-level specs must win over uv sources."""
    names = ["untaped"]
    names.extend(
        name
        for package in state.packages
        if is_package_name(name := plugin_package_key(package, reject_bare_direct=True))
    )
    return unique_packages(names)


def unique_packages(packages: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for package in packages:
        if package not in seen:
            unique.append(package)
            seen.add(package)
    return unique


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


def _run_command(cmd: list[str], failure: str) -> None:
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        rendered = " ".join(shlex.quote(part) for part in cmd)
        raise ConfigError(f"{failure} with exit {result.returncode}: {rendered}")
