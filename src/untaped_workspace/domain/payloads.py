"""Transport DTOs that cross the application/infrastructure boundary.

Putting these in ``domain/`` (rather than ``application/ports.py``) keeps
the import direction ``infrastructure → domain`` clean.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

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


class WorkspaceDetailRow(BaseModel):
    """One data row for ``workspace show`` output."""

    model_config = ConfigDict(frozen=True)

    workspace: str
    path: str
    default_branch: str | None
    repo_count: int
    repo: str
    url: str
    repo_branch: str | None
    target_branch: str | None


class BranchChange(BaseModel):
    """Manifest branch metadata changed by ``workspace branch`` commands."""

    model_config = ConfigDict(frozen=True)

    workspace: str
    repo: str | None
    branch: str | None


BranchApplyAction = Literal["checkout", "up-to-date", "skip", "unmatched"]
"""What ``workspace branch apply`` did or refused to do for one repo."""


class BranchApplyOutcome(BaseModel):
    """One row of ``workspace branch apply`` output."""

    model_config = ConfigDict(frozen=True)

    repo: str
    workspace: str
    target_branch: str | None
    action: BranchApplyAction
    detail: str = ""
