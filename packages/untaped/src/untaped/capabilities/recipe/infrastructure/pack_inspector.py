"""Filesystem-backed hook-project checks for the ``validate`` use case."""

from __future__ import annotations

from pathlib import Path

from untaped.capabilities.recipe.domain.pack import PackManifest
from untaped.capabilities.recipe.infrastructure.hook_resolver import HookResolver
from untaped.capabilities.recipe.infrastructure.pack_files import (
    LockFreshness,
    check_hook_project,
    read_hook_project,
)


class PackInspector:
    """Implements ``PackInspectorPort`` for one command invocation.

    Lockfile freshness (``uv lock --check``) is probed at most once per
    project root for the inspector's lifetime.
    """

    def __init__(self, *, library_root: Path) -> None:
        self._resolver = HookResolver(library_root=library_root)
        self._locks = LockFreshness()

    def read_hook_project(self, project_root: Path) -> PackManifest:
        """Read a local hook project's manifest (absent tables are empty)."""
        return read_hook_project(project_root)

    def check_hook_project(self, project_root: Path, manifest: PackManifest) -> None:
        """Validate hook metadata, ``uv.lock`` presence and freshness, and module files."""
        check_hook_project(project_root, manifest)
        if manifest.hooks:
            self._locks.check(project_root)

    def hook_exports(self, hook: str, local_hook_project: Path | None) -> frozenset[str]:
        """Resolve ``hook`` and return the entry points it exports."""
        return self._resolver.resolve(hook, local_hook_project).exports
