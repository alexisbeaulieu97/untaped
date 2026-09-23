"""Read/write ``<workspace-dir>/untaped.yml`` manifests."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from untaped.capabilities.workspace.domain import ManifestSource, WorkspaceManifest
from untaped.capabilities.workspace.errors import ManifestError
from untaped.capability_api import atomic_write, first_validation_error

MANIFEST_FILENAME = "untaped.yml"


class YamlManifestRepository:
    """Pydantic-validated round-trip for ``untaped.yml`` files."""

    def manifest_path(self, workspace_dir: Path) -> Path:
        return workspace_dir / MANIFEST_FILENAME

    def exists(self, workspace_dir: Path) -> bool:
        return self.manifest_path(workspace_dir).is_file()

    def read(self, workspace_dir: Path) -> WorkspaceManifest:
        path = self.manifest_path(workspace_dir)
        if not path.is_file():
            raise ManifestError(f"no manifest at {path} — run `untaped workspace init` first")
        try:
            raw = yaml.safe_load(_read_manifest_text(path)) or {}
        except yaml.YAMLError as exc:
            raise ManifestError(f"invalid YAML in {path}: {exc}") from exc
        try:
            return WorkspaceManifest.model_validate(raw)
        except ValidationError as exc:
            raise ManifestError(
                f"invalid manifest at {path}: {first_validation_error(exc)}"
            ) from exc

    def write(self, workspace_dir: Path, manifest: WorkspaceManifest) -> None:
        """Persist ``manifest`` to ``<workspace_dir>/untaped.yml``,
        creating ``workspace_dir`` if missing.

        The mkdir is load-bearing — it's how every bootstrap-style
        lifecycle command (``init``, ``adopt``, ``import``) obtains the
        workspace dir. See ``docs/workspace/usage.md`` for the user-facing
        manifest workflow.
        """
        path = self.manifest_path(workspace_dir)
        try:
            atomic_write(path, _dump(manifest), encoding="utf-8")
        except OSError as exc:
            raise ManifestError(f"could not write manifest at {path}: {exc}") from exc

    def delete(self, workspace_dir: Path) -> None:
        """Remove ``<workspace_dir>/untaped.yml`` if present."""
        path = self.manifest_path(workspace_dir)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise ManifestError(f"could not remove manifest at {path}: {exc}") from exc

    def read_external(self, source: Path) -> ManifestSource:
        """Read a manifest at an arbitrary path (used by ``import``)."""
        if not source.is_file():
            raise ManifestError(f"manifest not found: {source}")
        try:
            raw = yaml.safe_load(_read_manifest_text(source)) or {}
        except yaml.YAMLError as exc:
            raise ManifestError(f"invalid YAML in {source}: {exc}") from exc
        try:
            manifest = WorkspaceManifest.model_validate(raw)
        except ValidationError as exc:
            raise ManifestError(
                f"invalid manifest at {source}: {first_validation_error(exc)}"
            ) from exc
        return ManifestSource(manifest=manifest, source=source)


def _dump(manifest: WorkspaceManifest) -> str:
    data = manifest.model_dump(exclude_none=True, exclude_defaults=False)
    if not data.get("defaults"):
        data.pop("defaults", None)
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)


def _read_manifest_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ManifestError(f"could not read manifest at {path}: {exc}") from exc
