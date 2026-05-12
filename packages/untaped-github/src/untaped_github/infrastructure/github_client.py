"""HTTP client for the GitHub REST API."""

from __future__ import annotations

from collections.abc import Iterator
from types import TracebackType
from typing import Any

from untaped_core import ConfigError, HttpClient, HttpSettings
from untaped_core.http import resolve_verify

from untaped_github.infrastructure.config import GithubConfig
from untaped_github.infrastructure.pagination import paginate_list, paginate_search


class GithubClient:
    """Talks to ``api.github.com`` (or a GHE base) using the configured token."""

    def __init__(self, config: GithubConfig, *, http: HttpSettings | None = None) -> None:
        token = config.token.get_secret_value().strip() if config.token is not None else ""
        if not token:
            raise ConfigError(
                "github.token is not configured (set it via "
                "`untaped config set github.token <token>` or UNTAPED_GITHUB__TOKEN)"
            )
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": f"Bearer {token}",
        }
        self._http = HttpClient(
            base_url=config.base_url.rstrip("/"),
            headers=headers,
            verify=resolve_verify(http or HttpSettings()),
        )

    def me(self) -> dict[str, Any]:
        return self._http.get_json("/user")  # type: ignore[no-any-return]

    def search_repositories(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        return paginate_search(self._http, "/search/repositories", params=_q(q, sort), limit=limit)

    def search_code(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        return paginate_search(self._http, "/search/code", params=_q(q, sort), limit=limit)

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


def _q(query: str, sort: str | None) -> dict[str, str]:
    params = {"q": query}
    if sort:
        params["sort"] = sort
    return params
