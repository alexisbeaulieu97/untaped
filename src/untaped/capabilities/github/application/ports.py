"""Application-layer protocols (ports) for the GitHub bounded context."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from untaped.capabilities.github.domain import (
        CorpusFreshness,
        CorpusRepoResult,
        CorpusRepoTarget,
        GrepHit,
        RefSelector,
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
    """Local Git corpus operations used by sweep and cache commands."""

    def sync_repo(
        self,
        repo: CorpusRepoTarget,
        *,
        root: Path,
        selector: RefSelector,
        depth: int,
        auth_header: str | None,
    ) -> CorpusRepoResult: ...

    def repo_freshness(self, repo: CorpusRepoTarget, *, root: Path) -> CorpusFreshness | None: ...

    def local_refs(
        self,
        repo: CorpusRepoTarget,
        *,
        root: Path,
        selector: RefSelector,
    ) -> tuple[str, ...]: ...

    def grep_ref(
        self,
        repo: CorpusRepoTarget,
        *,
        root: Path,
        ref: str,
        pattern: str,
        paths: tuple[str, ...],
        ignore_case: bool,
        fixed_strings: bool,
        word_regexp: bool,
    ) -> tuple[GrepHit, ...]: ...

    def tree_paths(self, repo: CorpusRepoTarget, *, root: Path, ref: str) -> tuple[str, ...]: ...

    def read_blob(
        self,
        repo: CorpusRepoTarget,
        *,
        root: Path,
        ref: str,
        path: str,
    ) -> str | None: ...

    def validate_pattern(
        self,
        *,
        root: Path,
        pattern: str,
        paths: tuple[str, ...],
        fixed_strings: bool,
    ) -> str | None: ...

    def list_repos(self, *, root: Path) -> tuple[CorpusRepoResult, ...]: ...

    def get_repo(self, *, root: Path, repo: str) -> CorpusRepoTarget | None: ...

    def clean_repo(self, *, root: Path, repo: CorpusRepoResult) -> CorpusRepoResult: ...

    def materialize_worktree(
        self,
        repo: CorpusRepoTarget,
        *,
        root: Path,
        ref: str | None,
    ) -> WorktreeResult: ...
