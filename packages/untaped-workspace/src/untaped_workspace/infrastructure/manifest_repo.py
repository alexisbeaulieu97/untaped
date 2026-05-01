"""Read/write ``<workspace-dir>/untaped.yml`` manifests."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError
from untaped_core import first_validation_error

from untaped_workspace.domain import WorkspaceManifest
from untaped_workspace.errors import ManifestError

MANIFEST_FILENAME = "untaped.yml"


class ManifestRepository:
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
            raw = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise ManifestError(f"invalid YAML in {path}: {exc}") from exc
        try:
            return WorkspaceManifest.model_validate(raw)
        except ValidationError as exc:
            raise ManifestError(
                f"invalid manifest at {path}: {first_validation_error(exc)}"
            ) from exc

    def write(self, workspace_dir: Path, manifest: WorkspaceManifest) -> None:
        path = self.manifest_path(workspace_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(_dump(manifest))
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)

    def read_external(self, source: Path) -> WrittenManifest:
        """Read a manifest at an arbitrary path (used by ``import``)."""
        if not source.is_file():
            raise ManifestError(f"manifest not found: {source}")
        try:
            raw = yaml.safe_load(source.read_text()) or {}
        except yaml.YAMLError as exc:
            raise ManifestError(f"invalid YAML in {source}: {exc}") from exc
        try:
            manifest = WorkspaceManifest.model_validate(raw)
        except ValidationError as exc:
            raise ManifestError(
                f"invalid manifest at {source}: {first_validation_error(exc)}"
            ) from exc
        return WrittenManifest(manifest=manifest, source=source)


@dataclass(frozen=True, slots=True)
class WrittenManifest:
    """A loaded manifest plus its source path (for nicer error messages)."""

    manifest: WorkspaceManifest
    source: Path


def _dump(manifest: WorkspaceManifest) -> str:
    data = manifest.model_dump(exclude_none=True, exclude_defaults=False)
    if not data.get("defaults"):
        data.pop("defaults", None)
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
