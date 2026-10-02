"""Settings for the GitHub tool."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from untaped.sdk import TokenCommand, TokenSources


class SweepSettings(BaseModel):
    """Settings for sweep corpus refresh behavior."""

    model_config = ConfigDict(frozen=True)

    max_age_seconds: int = 3600
    sync_concurrency: int = 12


class InventorySettings(BaseModel):
    """Settings for the cached repository inventory workspace `create`/`add` and the picker use."""

    model_config = ConfigDict(frozen=True)

    path: Path = Path("~/.untaped/github-inventory.json")
    orgs: list[str] = Field(default_factory=list)
    teams: list[str] = Field(default_factory=list)
    max_age_seconds: int = Field(default=86400, ge=1)


class GithubSettings(BaseModel):
    """GitHub API settings."""

    token_sources: ClassVar[TokenSources] = TokenSources(env=("GH_TOKEN", "GITHUB_TOKEN"))

    model_config = ConfigDict(frozen=True)

    base_url: str = "https://api.github.com"
    token: SecretStr | None = None
    token_command: TokenCommand = None
    default_org: str | None = None
    corpus_path: Path = Path("~/.untaped/github-cache")
    sweep: SweepSettings = Field(default_factory=SweepSettings)
    inventory: InventorySettings = Field(default_factory=InventorySettings)
