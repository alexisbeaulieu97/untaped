"""The adapter for GitHub's REST API ``2022-11-28``, and its GraphQL API beside it."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any

from untaped.sdk.network import ApiAdapter, ApiMismatch, paginate_link
from untaped_github.application.ports import GithubApi
from untaped_github.domain.models import BatchRepoRefsResult
from untaped_github.infrastructure.graphql import (
    fetch_default_branch_refs,
    fetch_repo_refs,
    graphql_endpoint,
)
from untaped_github.settings import GithubSettings
from untaped_github.version import GithubVersion, Product

if TYPE_CHECKING:
    from untaped.sdk import HttpStatusError
    from untaped.sdk.network import HttpClient

#: The REST API version this adapter speaks, sent on every request.
API_VERSION = "2022-11-28"


def is_github_error(error: HttpStatusError) -> bool:
    """GitHub's own error answer carries a ``message`` (``{"message": "Not Found"}``)."""
    return '"message"' in (error.body or "")


def _watch() -> AbstractContextManager[None]:
    """A mismatch is a bare 404 or 405, or a 2xx that doesn't read; GitHub's own error is not."""
    return ApiMismatch.watch(vendor_error=is_github_error)


class Rest2022(ApiAdapter[GithubSettings, GithubVersion], GithubApi):
    """github.com, and GitHub Enterprise Server 3.9 and later.

    REST API 2022-11-28, with the `X-GitHub-Api-Version` header on every
    request; GraphQL beside it for batched ref probes.
    """

    recorded = (
        GithubVersion.parse("github.com"),
        GithubVersion.parse("GitHub Enterprise Server 3.14.2"),
    )

    @classmethod
    def serves(cls, version: GithubVersion) -> bool:
        """github.com, and GHES from 3.9, the first release that speaks this version."""
        if version.product is Product.DOTCOM:
            return True
        return version.release is not None and version.release >= (3, 9)

    def _client(self) -> HttpClient:
        return self.connect(
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION}
        )

    def me(self) -> dict[str, Any]:
        with _watch():
            return self._client().get_json_dict("/user")

    def get_repository(self, owner: str, repo: str) -> dict[str, Any]:
        with _watch():
            return self._client().get_json_dict(f"/repos/{owner}/{repo}")

    def list_org_repos(self, org: str) -> Iterator[dict[str, Any]]:
        return self._pages(f"/orgs/{org}/repos")

    def list_team_repos(self, org: str, team_slug: str) -> Iterator[dict[str, Any]]:
        return self._pages(f"/orgs/{org}/teams/{team_slug}/repos")

    def list_matching_refs(self, owner: str, repo: str, namespace: str) -> Iterator[dict[str, Any]]:
        return self._pages(f"/repos/{owner}/{repo}/git/matching-refs/{namespace}")

    def get_tree(
        self,
        owner: str,
        repo: str,
        tree_sha: str,
        *,
        recursive: bool = False,
    ) -> dict[str, Any]:
        params = {"recursive": "1"} if recursive else None
        with _watch():
            return self._client().get_json_dict(
                f"/repos/{owner}/{repo}/git/trees/{tree_sha}", params=params
            )

    def get_raw_content(self, owner: str, repo: str, path: str, *, ref: str) -> str:
        with _watch():
            response = self._client().get(
                f"/repos/{owner}/{repo}/contents/{path}",
                params={"ref": ref},
                headers={"Accept": "application/vnd.github.raw"},
            )
        return response.text

    def search_repositories(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        return self._search("/search/repositories", _q(q, sort), limit)

    def search_code(self, q: str, *, limit: int | None = None) -> Iterator[dict[str, Any]]:
        return self._search("/search/code", _q(q), limit)

    def search_issues(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        return self._search("/search/issues", _q(q, sort), limit)

    def search_users(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        return self._search("/search/users", _q(q, sort), limit)

    def batch_repo_refs(
        self,
        repos: Sequence[str],
        *,
        kinds: Sequence[str] = ("heads", "tags"),
        chunk_size: int = 50,
        concurrency: int = 1,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> BatchRepoRefsResult:
        with _watch():
            return fetch_repo_refs(
                self._client(),
                graphql_endpoint(self.settings.api_url),
                repos,
                kinds=kinds,
                chunk_size=chunk_size,
                concurrency=concurrency,
                on_progress=on_progress,
            )

    def batch_default_branch_refs(
        self,
        repos: Sequence[str],
        *,
        chunk_size: int = 100,
        concurrency: int = 1,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> BatchRepoRefsResult:
        with _watch():
            return fetch_default_branch_refs(
                self._client(),
                graphql_endpoint(self.settings.api_url),
                repos,
                chunk_size=chunk_size,
                concurrency=concurrency,
                on_progress=on_progress,
            )

    def _pages(
        self, path: str, *, params: dict[str, str] | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        """``path``'s rows across its ``Link`` pages, each page read under the watch."""
        with _watch():
            yield from paginate_link(self._client(), path, params=params, limit=limit)

    def _search(
        self, path: str, params: dict[str, str], limit: int | None
    ) -> Iterator[dict[str, Any]]:
        # Search pages nest their rows under ``items``; the counts beside them are unused.
        with _watch():
            yield from paginate_link(
                self._client(), path, params=params, limit=limit, item_key="items"
            )


def _q(query: str, sort: str | None = None) -> dict[str, str]:
    params = {"q": query}
    if sort:
        params["sort"] = sort
    return params
