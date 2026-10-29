"""HTTP client for the GitHub REST API."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from types import TracebackType
from typing import TYPE_CHECKING, Any

from untaped.sdk import paginate_link
from untaped_github.domain.models import BatchRepoRefsResult
from untaped_github.infrastructure.graphql import (
    fetch_default_branch_refs,
    fetch_repo_refs,
    graphql_endpoint,
)
from untaped_github.settings import TOKEN, GithubSettings

if TYPE_CHECKING:
    from untaped.sdk import HttpFactory


class GithubClient:
    """Talks to ``api.github.com`` (or a GHE base) using the server's stored token.

    ``http`` is the caller's ``self.ctx.http``, which adds untaped's proxy,
    trust, timeout and retries; a missing token fails fast with
    ``NoCredential``, naming ``untaped auth login github``.
    """

    def __init__(self, config: GithubSettings, *, http: HttpFactory) -> None:
        server = config.server
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._http = http.client(
            config,
            base_url=config.api_url,
            bearer=TOKEN.get(server),
            verify=server.verify_tls,
            headers=headers,
        )
        self._graphql_endpoint = graphql_endpoint(config.api_url)

    def me(self) -> dict[str, Any]:
        return self._http.get_json_dict("/user")

    def get_repository(self, owner: str, repo: str) -> dict[str, Any]:
        return self._http.get_json_dict(f"/repos/{owner}/{repo}")

    def list_org_repos(self, org: str) -> Iterator[dict[str, Any]]:
        return paginate_link(self._http, f"/orgs/{org}/repos")

    def list_matching_refs(self, owner: str, repo: str, namespace: str) -> Iterator[dict[str, Any]]:
        return paginate_link(self._http, f"/repos/{owner}/{repo}/git/matching-refs/{namespace}")

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

    def list_team_repos(self, org: str, team_slug: str) -> Iterator[dict[str, Any]]:
        return paginate_link(self._http, f"/orgs/{org}/teams/{team_slug}/repos")

    def _search(
        self, path: str, params: dict[str, str], limit: int | None
    ) -> Iterator[dict[str, Any]]:
        # Search pages nest their rows under ``items``; the counts beside them are unused.
        return paginate_link(self._http, path, params=params, limit=limit, item_key="items")

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
