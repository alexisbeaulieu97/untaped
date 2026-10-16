"""Source-index payload DTOs shared across application and infrastructure."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from untaped.sdk import ErrorCategory, FreshnessRecord, OutcomeRecord, UtcTimestamp
from untaped_ansible.domain.variables import FileAnalysis, VariableFact

ProbeFallbackReason = Literal["graphql_rate_limited", "graphql_transient"]
GRAPHQL_RATE_LIMIT_FALLBACK: ProbeFallbackReason = "graphql_rate_limited"
GRAPHQL_TRANSIENT_FALLBACK: ProbeFallbackReason = "graphql_transient"


class IndexedDependency(BaseModel):
    """One indexed dependency edge from a scanned source repo/ref."""

    model_config = ConfigDict(frozen=True)

    source_repo: str
    source_ref: str | None
    source_ref_kind: str | None = None
    dependency_name: str
    source_path: str
    source_sha: str | None = None
    dependency_repo: str | None = None
    dependency_version: str | None = None
    unresolved: str | None = None


class GitRef(BaseModel):
    """Resolved Git ref selected for dependency indexing."""

    model_config = ConfigDict(frozen=True)

    kind: str
    name: str
    sha: str


class ProbedRepo(BaseModel):
    """Refs and default branch reported by the freshness probe for one repo."""

    model_config = ConfigDict(frozen=True)

    default_branch: str | None = None
    refs: tuple[GitRef, ...] = ()


class ProbeTarget(BaseModel):
    """Repository metadata needed by remote ref probe backends."""

    model_config = ConfigDict(frozen=True)

    full_name: str
    default_branch: str
    clone_url: str | None = None
    ssh_url: str | None = None
    html_url: str | None = None


class ProbeFailure(BaseModel):
    """One repository that could not be probed for refs.

    ``category`` is the failure's category (``unavailable`` for a transient
    one), which the refresh's exit code follows.
    """

    model_config = ConfigDict(frozen=True)

    reason: str
    kind: Literal["missing", "transient", "chunk", "git"]
    category: ErrorCategory = ErrorCategory.FAILED


class ProbeReport(BaseModel):
    """Outcome of one batched ref freshness probe."""

    model_config = ConfigDict(frozen=True)

    repos: dict[str, ProbedRepo] = Field(default_factory=dict)
    failures: dict[str, ProbeFailure] = Field(default_factory=dict)
    fallbacks: dict[str, str] = Field(default_factory=dict)
    rate_limit_cost: int | None = None
    rate_limit_remaining: int | None = None
    rate_limit_reset_at: datetime | None = None


class RepoFailure(BaseModel):
    """One source repository that failed during a refresh (``category`` selects the exit code)."""

    model_config = ConfigDict(frozen=True)

    repo: str
    reason: str
    category: ErrorCategory = ErrorCategory.FAILED


class SkippedDependencyFile(BaseModel):
    """One file skipped while parsing dependencies or analysing variables."""

    model_config = ConfigDict(frozen=True)

    repo: str
    ref: str | None
    source_path: str
    reason: str


class SkippedRef(BaseModel):
    """One ref left out of a refresh because the repo store cannot hold its name."""

    model_config = ConfigDict(frozen=True)

    repo: str
    kind: Literal["heads", "tags"]
    ref: str
    reason: str


class CachedRef(BaseModel):
    """Cached ref metadata for graph display."""

    model_config = ConfigDict(frozen=True)

    name: str
    kind: str | None = None
    default_branch: str | None = None


class SourceRepoMetadata(BaseModel):
    """Cached metadata for one source repository."""

    model_config = ConfigDict(frozen=True)

    source_key: str
    source_repo: str
    default_branch: str


class RefScanMetadata(BaseModel):
    """Freshness metadata for one indexed source repo/ref."""

    model_config = ConfigDict(frozen=True)

    source_key: str
    source_repo: str
    ref_kind: str
    source_ref: str
    source_sha: str
    clone_url: str | None = None
    clone_protocol: str | None = None
    dependency_paths_fingerprint: str
    aliases_fingerprint: str = ""
    checked_at: datetime
    indexed_at: datetime


class RefScan(RefScanMetadata):
    """Replacement payload for one source repo/ref scan."""

    dependencies: tuple[IndexedDependency, ...] = ()


class RefScanTouch(BaseModel):
    """Freshness touch for one unchanged source repo/ref scan."""

    model_config = ConfigDict(frozen=True)

    source_key: str
    source_repo: str
    ref_kind: str
    source_ref: str
    checked_at: datetime


class SourceIndexStatus(BaseModel):
    """Summary of one indexed source."""

    model_config = ConfigDict(frozen=True)

    source_key: str
    scanned_at: UtcTimestamp
    repos: int
    refs: int
    edges: int


class SourceStatus(FreshnessRecord):
    """One row of ``source status`` (emitted as ``ansible.source_status``).

    Its freshness (``refreshed_at``, ``stale``, ``partial``, ``retry_at``…)
    is the cache's; ``scanned_at`` and the counts are when the index was
    last written and what it holds.
    """

    source: str
    source_key: str
    state: Literal["fresh", "stale", "not_refreshed", "missing_source"]
    configured: bool
    scanned_at: UtcTimestamp | None = None
    repos: int = 0
    refs: int = 0
    edges: int = 0


class AliasOutcome(OutcomeRecord):
    """Result of ``source-alias set``/``remove`` (kind ``ansible.source_alias_outcome``)."""

    alias: str
    repo: str


class SourceOutcome(OutcomeRecord):
    """Result of ``source set``, ``patch`` or ``remove`` (kind ``ansible.source_outcome``)."""

    name: str
    changes: list[str] = Field(default_factory=list)


class AnalysedFile(BaseModel):
    """One selected file's variable analysis, stored by its content and path."""

    model_config = ConfigDict(frozen=True)

    blob: str
    path: str
    analysis: FileAnalysis


class CommitListing(BaseModel):
    """The selected files one commit holds, as ``(path, blob)`` pairs, all analysed.

    Recording it marks the commit analysed: a query has facts for exactly
    the commits a listing was recorded for.
    """

    model_config = ConfigDict(frozen=True)

    repo: str
    sha: str
    files: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CommitFacts:
    """Variable facts read at some commits, keyed by the ``(repo, sha)`` asked for."""

    facts: dict[tuple[str, str], list[VariableFact]]
    """The facts naming one of the asked names, per analysed commit."""
    unanalysed: frozenset[tuple[str, str]]
    """The asked commits no listing was recorded for: their facts are unknown."""
    skipped: dict[tuple[str, str], list[tuple[str, str]]]
    """``(path, reason)`` of each file (or part of one) the analysis skipped, per commit."""
