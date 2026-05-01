"""Use case: return the authenticated GitHub user."""

from __future__ import annotations

from typing import Any, Protocol

from untaped_github.domain import GithubUser


class GithubMeService(Protocol):
    def me(self) -> dict[str, Any]: ...


class WhoAmI:
    """Validates ``GET /user`` payload into a domain entity."""

    def __init__(self, client: GithubMeService) -> None:
        self._client = client

    def __call__(self) -> GithubUser:
        return GithubUser.model_validate(self._client.me())
