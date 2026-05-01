"""Domain entities for the GitHub bounded context."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class GithubUser(BaseModel):
    """Authenticated GitHub user as returned by ``GET /user``."""

    model_config = ConfigDict(extra="ignore")

    login: str
    id: int
    name: str | None = None
    email: str | None = None
