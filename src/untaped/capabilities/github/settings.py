"""Settings for the GitHub tool."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, SecretStr


class SweepSettings(BaseModel):
    """Settings for sweep corpus refresh behavior."""

    max_age_seconds: int = 3600
    sync_concurrency: int = 12


class GithubSettings(BaseModel):
    """GitHub API settings."""

    base_url: str = "https://api.github.com"
    token: SecretStr | None = None
    corpus_path: Path = Path("~/.untaped/github-corpus")
    sweep: SweepSettings = Field(default_factory=SweepSettings)
