"""HTTP client for the GitHub REST API."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from types import TracebackType
from typing import Any

from untaped.api import HttpSettings, connected_client
from untaped.capabilities.github.domain.models import BatchRepoRefsResult
from untaped.capabilities.github.infrastructure.graphql import (
    fetch_default_branch_refs,
    fetch_repo_refs,
    graphql_endpoint,
)
from untaped.capabilities.github.infrastructure.pagination import paginate_list, paginate_search
from untaped.capabilities.github.settings import GithubSettings


class GithubClient:
    """Talks to ``api.github.com`` (or a GHE base) using the configured token.

    Connection wiring (required-field validation, bearer auth, TLS, base-URL
    normalization) is delegated to core's ``connected_client``; a missing or
    blank token fail-fasts with the standard ``ConfigError``. ``base_url``
    stays in ``required`` despite its non-empty default so an explicit blank
    override still fails loudly.
    """

    def __init__(self, config: GithubSettings, *, http: HttpSettings | None = None) -> None:
        self._http = connected_client(
            config,
            section="github",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            http=http,
        )
        self._graphql_endpoint = graphql_endpoint(config.base_url)

    def me(self) -> dict[str, Any]:
        return self._http.get_json_dict("/user")

    def get_repository(self, owner: str, repo: str) -> dict[str, Any]:
        return self._http.get_json_dict(f"/repos/{owner}/{repo}")

    def list_org_repos(self, org: str) -> Iterator[dict[str, Any]]:
        return paginate_list(self._http, f"/orgs/{org}/repos")

    def list_matching_refs(self, owner: str, repo: str, namespace: str) -> Iterator[dict[str, Any]]:
        return paginate_list(self._http, f"/repos/{owner}/{repo}/git/matching-refs/{namespace}")

    def get_tree(
        self,
        owner: str,
        repo: str,
        tree_sha: str,
        *,
        recursive: bool = False,
    ) -> dict[str, Any]:
        params = {"recursive": "1"} if recursive else None
        return self._http.get_json_dict(
            f"/repos/{owner}/{repo}/git/trees/{tree_sha}",
            params=params,
        )

    def get_raw_content(self, owner: str, repo: str, path: str, *, ref: str) -> str:
        response = self._http.get(
            f"/repos/{owner}/{repo}/contents/{path}",
            params={"ref": ref},
            headers={"Accept": "application/vnd.github.raw"},
        )
        return response.text

    def search_repositories(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        return paginate_search(self._http, "/search/repositories", params=_q(q, sort), limit=limit)

    def search_code(self, q: str, *, limit: int | None = None) -> Iterator[dict[str, Any]]:
        return paginate_search(self._http, "/search/code", params=_q(q), limit=limit)

    def search_issues(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        return paginate_search(self._http, "/search/issues", params=_q(q, sort), limit=limit)

    def search_users(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        return paginate_search(self._http, "/search/users", params=_q(q, sort), limit=limit)

    def list_team_repos(self, org: str, team_slug: str) -> Iterator[dict[str, Any]]:
        return paginate_list(self._http, f"/orgs/{org}/teams/{team_slug}/repos")

    def batch_repo_refs(
        self,
        repos: Sequence[str],
        *,
        kinds: Sequence[str] = ("heads", "tags"),
        chunk_size: int = 50,
    ) -> BatchRepoRefsResult:
        """Probe branch/tag heads for many ``owner/name`` repos via GraphQL.

        Batches ``chunk_size`` repos per aliased GraphQL POST, so ~1500
        repos resolve in ~30 API calls. ``kinds`` selects the ref
        namespaces; passing only ``("heads",)`` omits the tags
        connection and halves the GraphQL point cost. Transient 5xx or
        transport failures are returned per repo in
        ``BatchRepoRefsResult.failures`` when adaptive retries cannot
        resolve them.
        """
        return fetch_repo_refs(
            self._http,
            self._graphql_endpoint,
            repos,
            kinds=kinds,
            chunk_size=chunk_size,
        )

    def batch_default_branch_refs(
        self,
        repos: Sequence[str],
        *,
        chunk_size: int = 200,
    ) -> BatchRepoRefsResult:
        """Probe only default-branch heads for many ``owner/name`` repos via GraphQL.

        Uses the same transient failure reporting contract as
        :meth:`batch_repo_refs`.
        """
        return fetch_default_branch_refs(
            self._http,
            self._graphql_endpoint,
            repos,
            chunk_size=chunk_size,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GithubClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def _q(query: str, sort: str | None = None) -> dict[str, str]:
    params = {"q": query}
    if sort:
        params["sort"] = sort
    return params
