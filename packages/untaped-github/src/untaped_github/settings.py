"""Settings for the GitHub tool."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from untaped.sdk import Retired, TokenCommand, TokenSources


class SweepSettings(BaseModel):
    """Settings for sweep corpus refresh behavior."""

    model_config = ConfigDict(frozen=True)

    max_age_seconds: int = Field(default=3600, ge=0)
    parallel: int = Field(default=12, ge=1)


class InventorySettings(BaseModel):
    """The orgs and teams whose repos github lists for workspace (its ``RepoSource``)."""

    model_config = ConfigDict(frozen=True)

    orgs: list[str] = Field(default_factory=list)
    teams: list[str] = Field(default_factory=list)


_INVENTORY_NOTE = (
    "deleted in 11.0; workspace keeps the repos github lists for 6 hours "
    "(ctrl-r in its picker refreshes them)"
)


class GithubSettings(BaseModel):
    """GitHub API settings."""

    token_sources: ClassVar[TokenSources] = TokenSources(env=("GH_TOKEN", "GITHUB_TOKEN"))
    retired_keys: ClassVar[Mapping[str, str | Retired]] = {
        "corpus_path": "cache_dir",
        "cache_dir": Retired(note="deleted in 11.0; the repo store lives under git.store_dir"),
        "sweep.sync_concurrency": "sweep.parallel",
        "inventory.path": Retired(note=_INVENTORY_NOTE),
        "inventory.max_age_seconds": Retired(note=_INVENTORY_NOTE),
    }

    model_config = ConfigDict(frozen=True)

    base_url: str = "https://api.github.com"
    token: SecretStr | None = None
    token_command: TokenCommand = None
    default_org: str | None = None
    git_protocol: Literal["https", "ssh"] = "https"
    sweep: SweepSettings = Field(default_factory=SweepSettings)
    inventory: InventorySettings = Field(default_factory=InventorySettings)
