"""Domain value objects for local Git corpus operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from untaped.capabilities.github.domain.sweep import RefProfile, RefSelector, profile_join


@dataclass(frozen=True)
class CorpusFailure:
    """A per-repository corpus failure that does not discard other successes."""

    repo: str
    reason: str


@dataclass(frozen=True)
class CorpusRepoTarget:
    """Repository metadata needed by local Git corpus operations."""

    full_name: str
    default_branch: str | None
    clone_url: str | None = None
    html_url: str | None = None
    archived: bool = False


@dataclass(frozen=True)
class CorpusFreshness:
    """Fetch metadata for one repository in the local corpus."""

    fetched_at: datetime
    profile: RefProfile
    ref_globs: tuple[str, ...] = ()
    archived: bool = False


@dataclass(frozen=True)
class GrepHit:
    """One content match within a cached Git blob."""

    path: str
    line: int
    text: str
    blob_oid: str


def covers(freshness: CorpusFreshness, selector: RefSelector) -> bool:
    """Return whether cached metadata already covers the requested selector."""
    return profile_join(freshness.profile, selector.profile) == freshness.profile and set(
        selector.globs
    ).issubset(freshness.ref_globs)
