"""Application-layer protocols (ports) for the GitHub bounded context."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol


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

    def search_code(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]: ...

    def search_issues(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]: ...

    def search_users(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]: ...


class GithubTeamService(Protocol):
    """Team membership lookup, used by ``--team`` resolution."""

    def list_team_repos(self, org: str, team_slug: str) -> Iterator[dict[str, Any]]: ...
