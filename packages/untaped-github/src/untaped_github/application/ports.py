"""Application-layer protocols (ports) for the GitHub bounded context."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from datetime import datetime

    from untaped_github.domain import (
        CorpusFreshness,
        CorpusRepoResult,
        CorpusRepoTarget,
        GrepHit,
        GrepSpec,
        LocalRef,
        RefSelector,
        RepoInventory,
        WorktreeResult,
    )


class GithubMeService(Protocol):
    """The authenticated-user fetch contract that ``WhoAmI`` depends on."""

    def me(self) -> dict[str, Any]: ...


class GithubSearchService(Protocol):
    """Search endpoints used by the four ``Search*`` use cases.

    Adapters are expected to handle pagination internally (GitHub's
    Link-header walk) and honour ``limit`` so use cases never see the
    raw page boundaries. ``limit=None`` means unbounded (paginate
    until exhausted or GitHub's 1000-result cap). The CLI always
    supplies an int; ``None`` exists for non-CLI callers (tests,
    programmatic use).
    """

    def search_repositories(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]: ...

    def search_code(self, q: str, *, limit: int | None = None) -> Iterator[dict[str, Any]]: ...

    def search_issues(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]: ...

    def search_users(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]: ...


class GithubTeamService(Protocol):
    """Team membership lookup, used by ``--team`` resolution."""

    def list_team_repos(self, org: str, team_slug: str) -> Iterator[dict[str, Any]]: ...


class GithubRepoListService(Protocol):
    """Repository inventory endpoints used by ``repos list``."""

    def list_org_repos(self, org: str) -> Iterator[dict[str, Any]]: ...

    def list_team_repos(self, org: str, team_slug: str) -> Iterator[dict[str, Any]]: ...


class GithubRepositoryInventoryService(GithubRepoListService, Protocol):
    """Repository metadata endpoints used by reusable inventory expansion."""

    def get_repository(self, owner: str, repo: str) -> dict[str, Any]: ...


class GitCorpus(Protocol):
    """Local Git corpus operations used by sweep and cache commands.

    The corpus lives in the git plugin's repo store: the adapter decides where
    each repo is and how it is fetched, so no call takes a root or a depth.
    """

    def sync_repo(self, repo: CorpusRepoTarget, *, selector: RefSelector) -> CorpusRepoResult: ...

    def repo_freshness(self, repo: CorpusRepoTarget) -> CorpusFreshness | None: ...

    def touch_repo(self, repo: CorpusRepoTarget) -> datetime:
        """Record that the cached copy is current without fetching; return the new time."""
        ...

    def local_refs(
        self, repo: CorpusRepoTarget, *, selector: RefSelector
    ) -> tuple[LocalRef, ...]: ...

    def grep_trees(
        self, repo: CorpusRepoTarget, *, trees: tuple[str, ...], spec: GrepSpec
    ) -> dict[str, tuple[GrepHit, ...]]:
        """Grep several trees in one pass; trees without hits are absent."""
        ...

    def tree_has_match(self, repo: CorpusRepoTarget, *, tree: str, spec: GrepSpec) -> bool:
        """Return whether ``spec`` matches anywhere in ``tree``, stopping at the first hit."""
        ...

    def tree_paths(self, repo: CorpusRepoTarget, *, ref: str) -> tuple[str, ...]: ...

    def read_first_blob(
        self, repo: CorpusRepoTarget, *, ref: str, paths: tuple[str, ...]
    ) -> str | None:
        """Read the first of ``paths`` that exists in ``ref``; None when none does."""
        ...

    def validate_pattern(
        self, *, pattern: str, paths: tuple[str, ...], fixed_strings: bool
    ) -> str | None: ...

    def list_repos(self) -> tuple[CorpusRepoResult, ...]: ...

    def get_repo(self, repo: str) -> CorpusRepoTarget | None: ...

    def clean_repo(self, repo: CorpusRepoResult) -> CorpusRepoResult:
        """Release ``repo``: ``removed`` with the bytes freed, or ``released`` and who kept it."""
        ...

    def materialize_worktree(
        self, repo: CorpusRepoTarget, *, ref: str | None
    ) -> WorktreeResult: ...


class InventoryStore(Protocol):
    """Where the cached repository inventory lives."""

    def load(self) -> RepoInventory | None:
        """The saved inventory, or ``None`` when there is none or it is unreadable."""
        ...

    def save(self, inventory: RepoInventory) -> None:
        """Replace the saved inventory atomically."""
        ...

    def lock(self) -> AbstractContextManager[None]:
        """Serialize refreshes across processes."""
        ...
