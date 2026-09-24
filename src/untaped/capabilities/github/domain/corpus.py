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
    # GitHub's last-push timestamp; None when the source did not report it.
    pushed_at: str | None = None


@dataclass(frozen=True)
class CorpusFreshness:
    """Fetch metadata for one repository in the local corpus."""

    fetched_at: datetime
    profile: RefProfile
    ref_globs: tuple[str, ...] = ()
    archived: bool = False
    pushed_at: str | None = None
    default_branch: str | None = None


@dataclass(frozen=True)
class LocalRef:
    """One cached ref and the tree it points at; refs often share a tree."""

    name: str
    tree: str


@dataclass(frozen=True)
class GrepSpec:
    """One content pattern plus the modifiers every ``git grep`` call shares."""

    pattern: str
    paths: tuple[str, ...] = ()
    ignore_case: bool = False
    fixed_strings: bool = False
    word_regexp: bool = False


@dataclass(frozen=True)
class GrepHit:
    """One content match within a cached Git tree."""

    path: str
    line: int
    text: str


def covers(freshness: CorpusFreshness, selector: RefSelector) -> bool:
    """Return whether cached metadata already covers the requested selector."""
    return profile_join(freshness.profile, selector.profile) == freshness.profile and set(
        selector.globs
    ).issubset(freshness.ref_globs)


def unchanged_upstream(freshness: CorpusFreshness, repo: CorpusRepoTarget) -> bool:
    """Return whether GitHub reports no push since the cached copy was fetched.

    Needs a ``pushed_at`` on both sides and the same default branch, so a
    renamed default branch still triggers a fetch.
    """
    return (
        repo.pushed_at is not None
        and freshness.pushed_at == repo.pushed_at
        and freshness.default_branch == repo.default_branch
    )
