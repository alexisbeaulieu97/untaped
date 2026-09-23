"""Transport DTOs that cross the application/infrastructure boundary.

Putting these in ``domain/`` (rather than ``application/ports.py``) keeps
the import direction ``infrastructure → domain`` clean.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from untaped.capabilities.workspace.domain.manifest import WorkspaceManifest
from untaped.capability_api import OutcomeRecord, TargetRecord


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


class BareCacheEntry(BaseModel):
    """A bare-cache repo path plus whether it was created by this call."""

    model_config = ConfigDict(frozen=True)

    path: Path
    created: bool


class WorkspaceSummaryRow(BaseModel):
    """The single ``workspace get`` row of a workspace with no repos.

    Emitted as ``workspace.repo.summary``: like every ``.summary`` row it
    carries no ``target_path``, so filesystem consumers can skip it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace: str
    path: str
    default_branch: str | None
    repo_count: int
    repo: str
    url: str
    repo_branch: str | None
    target_branch: str | None


class WorkspaceDetailRow(TargetRecord):
    """One repo row of ``workspace get`` output; ``target_path`` is the clone directory."""

    workspace: str
    path: str
    default_branch: str | None
    repo_count: int
    repo: str
    url: str
    repo_branch: str | None
    target_branch: str | None


class BranchChange(OutcomeRecord):
    """Manifest branch metadata changed by ``workspace branch`` commands."""

    workspace: str
    repo: str | None
    branch: str | None
    action: str = "updated"


class WorkspaceOutcome(OutcomeRecord, TargetRecord):
    """One row of ``workspace init`` / ``workspace forget`` output.

    ``action`` is ``created`` (init), ``forgotten`` or ``pruned`` (forget);
    ``target_path`` is the workspace directory.
    """

    name: str
    action: str


class RepoAddOutcome(OutcomeRecord, TargetRecord):
    """One row of ``workspace add`` output; ``target_path`` is the clone directory."""

    workspace: str
    repo: str
    url: str
    branch: str | None
    action: str = "added"


class RepoRemoveOutcome(OutcomeRecord):
    """One row of ``workspace remove`` output.

    ``repo`` is the identifier as given for ``planned`` (``--dry-run``)
    rows and the manifest name for ``removed`` rows; ``pruned`` says
    whether the local clone is (or would be) deleted as well.
    """

    workspace: str
    repo: str
    action: str
    pruned: bool


BranchApplyAction = Literal["checked_out", "unchanged", "skipped", "failed", "unmatched"]
"""What ``workspace branch apply`` did or refused to do for one repo.

``skipped`` is an intentional refusal; ``failed`` means a fetch, status, or
checkout attempt errored.
"""


class BranchApplyOutcome(OutcomeRecord, TargetRecord):
    """One row of ``workspace branch apply`` output."""

    repo: str
    workspace: str
    target_branch: str | None
    action: BranchApplyAction
    detail: str = ""
