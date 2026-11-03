"""Application-layer protocols (ports) for the GitHub bounded context."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from typing import TYPE_CHECKING, Any, Protocol

from untaped.sdk.storage import NO_PROGRESS

if TYPE_CHECKING:
    from datetime import datetime

    from untaped.sdk.storage import ProgressHandle
    from untaped_github.domain import (
        CorpusFreshness,
        CorpusRepoTarget,
        GrepHit,
        GrepSpec,
        LocalRef,
        RefSelector,
    )
    from untaped_github.domain.models import BatchRepoRefsResult
    from untaped_github.records import CacheDeleteOutcome, CorpusRepo, Worktree


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


class GithubApi(GithubMeService, GithubSearchService, GithubRepositoryInventoryService, Protocol):
    """github's port: every call its commands, providers and ansible make to the GitHub API.

    One adapter per REST API version serves it
    (:class:`untaped_github.infrastructure.rest_2022.Rest2022`); commands reach it
    through ``SERVER_API.port(settings)``. Rows are GitHub's JSON objects.
    """

    def list_matching_refs(
        self, owner: str, repo: str, namespace: str
    ) -> Iterator[dict[str, Any]]: ...

    def get_tree(
        self, owner: str, repo: str, tree_sha: str, *, recursive: bool = False
    ) -> dict[str, Any]: ...

    def get_raw_content(self, owner: str, repo: str, path: str, *, ref: str) -> str: ...

    def batch_repo_refs(
        self,
        repos: Sequence[str],
        *,
        kinds: Sequence[str] = ("heads", "tags"),
        chunk_size: int = 50,
        concurrency: int = 1,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> BatchRepoRefsResult:
        """Probe branch and tag heads for many ``owner/name`` repos in batched GraphQL queries.

        Batches ``chunk_size`` repos per query, ``concurrency`` at once;
        ``kinds`` selects the ref namespaces, and passing only ``("heads",)``
        halves the GraphQL cost. ``on_progress(done, total)`` hears of each
        finished chunk in repos. A chunk whose request fails for any reason
        but a GraphQL error (which stops the probe) returns its repos in
        ``BatchRepoRefsResult.failures``.
        """
        ...

    def batch_default_branch_refs(
        self,
        repos: Sequence[str],
        *,
        chunk_size: int = 100,
        concurrency: int = 1,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> BatchRepoRefsResult:
        """Probe only default-branch heads, as :meth:`batch_repo_refs` probes and reports."""
        ...


class GitCorpus(Protocol):
    """Local Git corpus operations used by sweep and cache commands.

    The corpus lives in the git plugin's repo store: the adapter decides where
    each repo is and how it is fetched, so no call takes a root or a depth.
    """

    def sync_repo(
        self,
        repo: CorpusRepoTarget,
        *,
        selector: RefSelector,
        progress: ProgressHandle = NO_PROGRESS,
    ) -> CorpusRepo: ...

    def repo_freshness(self, repo: CorpusRepoTarget) -> CorpusFreshness | None: ...

    def cache_key(self, repo: CorpusRepoTarget) -> str:
        """The key of ``repo``'s stored copy: one per store repo, whatever its URL's spelling."""
        ...

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

    def list_repos(self) -> tuple[CorpusRepo, ...]: ...

    def get_repo(self, repo: str) -> CorpusRepoTarget | None: ...

    def clean_repo(self, repo: CorpusRepo) -> CacheDeleteOutcome:
        """Release ``repo``: ``deleted`` with the bytes freed, or ``released`` and who kept it."""
        ...

    def materialize_worktree(self, repo: CorpusRepoTarget, *, ref: str | None) -> Worktree: ...
