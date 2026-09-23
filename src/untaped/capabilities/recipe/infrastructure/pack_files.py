"""File-backed reads and checks of pack projects.

Reads ``pyproject.toml`` manifests, scans hook module files, and enforces the
hook-project file contract (compatible metadata, a ``uv.lock`` when hooks are
declared, and a module file per hook) in one place for ``add``, ``validate``, and
hook resolution.
"""

from __future__ import annotations

import tomllib
from functools import cache
from importlib.metadata import version
from pathlib import Path

from untaped.capabilities.recipe.domain.hook_exports import hook_exports_from_source
from untaped.capabilities.recipe.domain.hook_project import (
    hook_module_file,
    untaped_dev_requirement,
    validate_hook_project_contract,
)
from untaped.capabilities.recipe.domain.pack import PackManifest
from untaped.capabilities.recipe.infrastructure.uv_project import check_lock


@cache
def installed_dev_requirement() -> str:
    """The dev-only ``untaped`` requirement matching the running installation."""
    return untaped_dev_requirement(version("untaped"))


def read_pack_manifest(project_root: Path) -> PackManifest:
    """Read a pack manifest; ``[project]`` and ``[tool.untaped_recipe]`` are required."""
    return _read_manifest(project_root, label="pack", require_pack=True)


def read_hook_project(project_root: Path) -> PackManifest:
    """Read a local hook project's manifest; absent tables yield an empty manifest."""
    return _read_manifest(project_root, label="hook", require_pack=False)


def hook_exports(module_file: Path) -> frozenset[str]:
    """Scan a hook module file for entry points; never import it."""
    try:
        return hook_exports_from_source(module_file.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as error:
        raise ValueError(f"cannot scan hook module {module_file}: {error}") from error


def check_hook_project(project_root: Path, manifest: PackManifest) -> None:
    """Enforce the hook-project contract: metadata, ``uv.lock``, and module files.

    A lockfile is required only when the project declares hooks; a hookless
    pack passes without one.
    """
    validate_hook_project_contract(
        project_root,
        manifest,
        dev_requirement=installed_dev_requirement(),
    )
    if manifest.hooks and not (project_root / "uv.lock").is_file():
        raise ValueError(f"pack project is missing uv.lock: {project_root}")
    for definition in manifest.hooks.values():
        module_file = hook_module_file(project_root, definition.module)
        if not module_file.is_file():
            raise ValueError(f"hook module file not found: {module_file}")


class LockFreshness:
    """Probe ``uv lock --check`` at most once per project root per command."""

    def __init__(self) -> None:
        self._results: dict[Path, str | None] = {}

    def check(self, project_root: Path) -> None:
        """Raise when the project's ``uv.lock`` is stale (cached per root)."""
        key = project_root.resolve()
        if key not in self._results:
            try:
                check_lock(project_root)
            except ValueError as exc:
                self._results[key] = str(exc)
            else:
                self._results[key] = None
        error = self._results[key]
        if error is not None:
            raise ValueError(error)


def _read_manifest(project_root: Path, *, label: str, require_pack: bool) -> PackManifest:
    pyproject = project_root / "pyproject.toml"
    if not pyproject.is_file():
        raise ValueError(f"{label} project must contain pyproject.toml: {project_root}")
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid {label} project pyproject: {pyproject}: {exc}") from exc
    return PackManifest.from_pyproject(data, source=pyproject, require_pack=require_pack)
