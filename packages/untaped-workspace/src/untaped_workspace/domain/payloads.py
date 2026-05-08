"""Transport DTOs that cross the application/infrastructure boundary.

Mirrors :mod:`untaped_awx.domain.payloads`. Putting these in ``domain/``
(rather than ``application/ports.py``) keeps the import direction
``infrastructure → domain`` clean.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from untaped_workspace.domain.manifest import WorkspaceManifest


class DiscoveredRepo(BaseModel):
    """A clone discovered on disk during :class:`AdoptWorkspace`."""

    model_config = ConfigDict(frozen=True)

    name: str
    url: str
    branch: str | None


class DiscoveryResult(BaseModel):
    """Output of :meth:`RepoDiscoverer.discover`: kept repos plus
    human-readable reasons (one per skipped child) for the application
    to surface."""

    model_config = ConfigDict(frozen=True)

    repos: list[DiscoveredRepo]
    skipped: list[str]


class ManifestSource(BaseModel):
    """A manifest loaded from an arbitrary path plus its source (for
    nicer error messages). Returned by
    :meth:`ManifestRepository.read_external`."""

    model_config = ConfigDict(frozen=True)

    manifest: WorkspaceManifest
    source: Path
