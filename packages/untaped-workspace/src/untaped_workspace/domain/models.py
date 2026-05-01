"""Domain entities for the workspace bounded context."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class Workspace(BaseModel):
    """A registry entry: a named directory on disk.

    Repos live in the per-workspace ``untaped.yml`` manifest, not here. Use
    :class:`WorkspaceManifest` (loaded via the infrastructure
    ``ManifestRepo``) to access them.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    path: Path
