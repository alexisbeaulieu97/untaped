"""Application-layer ports implemented by the infrastructure adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from untaped.capabilities.recipe.domain.pack import (
    InstalledPack,
    PackManifest,
    PackRef,
    RecipeEntry,
)
from untaped.capabilities.recipe.domain.plan import HookDebugResult, Verdict


class HookExecutorPort(Protocol):
    """Execute trusted hooks through their resolved runtime."""

    def transform(
        self,
        hook: str,
        content: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        file: Path,
        inputs: dict[str, object],
        args: dict[str, object],
        capture_diagnostics: bool = False,
    ) -> HookDebugResult[str]:
        """Run a transform hook and return replacement content plus diagnostics."""

    def validate(
        self,
        hook: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        inputs: dict[str, object],
        args: dict[str, object],
        capture_diagnostics: bool = False,
    ) -> HookDebugResult[Verdict]:
        """Run a validate hook and return its coerced verdict plus diagnostics."""


class PackLibraryPort(Protocol):
    """Installed pack library lookups, plus explicit-path packs."""

    @property
    def packs_dir(self) -> Path:
        """Directory containing installed pack copies."""

    def packs(self) -> list[InstalledPack]:
        """Installed packs that loaded cleanly."""

    def load_errors(self) -> dict[str, str]:
        """``{pack name: error}`` for installed packs that failed to load."""

    def reconcile(self) -> list[str]:
        """Index/directory consistency problems."""

    def find_pack(self, name: str) -> InstalledPack | None:
        """The installed pack named ``name``, if any."""

    def find_recipe(self, ref: PackRef) -> tuple[InstalledPack, RecipeEntry]:
        """Resolve a bare or qualified recipe reference."""

    def local_pack(self, path: Path) -> InstalledPack:
        """Read an explicit-path pack that is not tracked by the library index."""


class PackInspectorPort(Protocol):
    """File-backed hook-project checks used by ``check``."""

    def read_hook_project(self, project_root: Path) -> PackManifest:
        """Read a local hook project's manifest (absent tables are empty)."""

    def check_hook_project(self, project_root: Path, manifest: PackManifest) -> None:
        """Validate hook metadata, ``uv.lock`` presence and freshness, and module files."""

    def hook_exports(self, hook: str, local_hook_project: Path | None) -> frozenset[str]:
        """Resolve ``hook`` and return the entry points it exports."""
