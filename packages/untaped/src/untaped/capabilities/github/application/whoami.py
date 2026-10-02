"""Use case: return the authenticated GitHub user."""

from __future__ import annotations

from untaped.capabilities.github.application.ports import GithubMeService
from untaped.capabilities.github.domain import GithubUser


class WhoAmI:
    """Validates ``GET /user`` payload into a domain entity."""

    def __init__(self, client: GithubMeService) -> None:
        self._client = client

    def __call__(self) -> GithubUser:
        return GithubUser.model_validate(self._client.me())
