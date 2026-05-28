"""Settings model contributed by the GitHub plugin."""

from __future__ import annotations

from pydantic import BaseModel, SecretStr


class GithubSettings(BaseModel):
    """GitHub API settings."""

    base_url: str = "https://api.github.com"
    token: SecretStr | None = None
