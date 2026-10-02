"""Application-layer ports implemented by the infrastructure adapters."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from untaped_recipe.domain.pack import (
    InstalledPack,
    PackManifest,
    PackRef,
    RecipeEntry,
)
from untaped_recipe.domain.plan import HookDebugResult, Verdict

if TYPE_CHECKING:
    from untaped_recipe.infrastructure.hook_resolver import UvHookRef
    from untaped_recipe.infrastructure.hook_worker_client import (
        HookWorkerCallResult,
    )


class PromptFunc(Protocol):
    """Prompt callback asking for a missing required input on a terminal."""

    def __call__(self, message: str, *, sensitive: bool) -> object: ...


class HookWorkerPort(Protocol):
    """Request/response transport for external hook execution."""

    def request(
        self,
        ref: UvHookRef,
        payload: dict[str, object],
        *,
        diagnostic_limit: int | None = ...,
        settle_seconds: float = ...,
    ) -> HookWorkerCallResult:
        """Send one hook request and return the validated result plus diagnostics."""


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

    def reconcile(self) -> dict[str, str]:
        """``{pack name: problem}`` for index/directory consistency problems."""

    def find_pack(self, name: str) -> InstalledPack | None:
        """The installed pack named ``name``, if any."""

    def find_recipe(self, ref: PackRef) -> tuple[InstalledPack, RecipeEntry]:
        """Resolve a bare or qualified recipe reference.

        A miss raises :class:`~untaped_recipe.errors.RecipeNotFoundError`;
        a bare ref several packs export raises
        :class:`~untaped_recipe.errors.AmbiguousRefError`. Both are
        ``ValueError`` subclasses.
        """

    def local_pack(self, path: Path) -> InstalledPack:
        """Read an explicit-path pack that is not tracked by the library index."""


class PackInspectorPort(Protocol):
    """File-backed hook-project checks used by ``validate``."""

    def read_hook_project(self, project_root: Path) -> PackManifest:
        """Read a local hook project's manifest (absent tables are empty)."""

    def check_hook_project(self, project_root: Path, manifest: PackManifest) -> None:
        """Validate hook metadata, ``uv.lock`` presence and freshness, and module files."""

    def hook_exports(self, hook: str, local_hook_project: Path | None) -> frozenset[str]:
        """Resolve ``hook`` and return the entry points it exports."""


class HookHelpers(Protocol):
    """Helper methods available to external hook projects."""

    def pass_(self, message: str = "") -> dict[str, str]:
        """Return a passing validation verdict."""

    def fail(self, message: str) -> dict[str, str]:
        """Return a failing validation verdict."""

    def skip(self, message: str = "") -> dict[str, str]:
        """Return a skip verdict marking the target not applicable."""

    def warn(self, message: str) -> None:
        """Accumulate a non-fatal warning for the current target."""

    def render_template(
        self,
        template: str,
        inputs: dict[str, object],
        *,
        unknown_tokens: str = "error",
    ) -> str:
        """Render simple recipe placeholders."""

    def load_yaml(self, content: str) -> object:
        """Round-trip-load YAML content."""

    def dump_yaml(self, data: object, *, options: Mapping[str, object] | None = None) -> str:
        """Round-trip-dump YAML data with optional formatting controls."""
