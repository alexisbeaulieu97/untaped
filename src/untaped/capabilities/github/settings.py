"""Settings for the GitHub tool."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from untaped.capability_api import TokenCommand, TokenSources


class SweepSettings(BaseModel):
    """Settings for sweep corpus refresh behavior."""

    model_config = ConfigDict(frozen=True)

    max_age_seconds: int = 3600
    sync_concurrency: int = 12


class GithubSettings(BaseModel):
    """GitHub API settings."""

    token_sources: ClassVar[TokenSources] = TokenSources(env=("GH_TOKEN", "GITHUB_TOKEN"))

    model_config = ConfigDict(frozen=True)

    base_url: str = "https://api.github.com"
    token: SecretStr | None = None
    token_command: TokenCommand = None
    corpus_path: Path = Path("~/.untaped/github-corpus")
    sweep: SweepSettings = Field(default_factory=SweepSettings)
