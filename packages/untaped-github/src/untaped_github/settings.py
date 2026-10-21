"""Settings for the GitHub plugin."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from untaped.sdk import NeedsSetup, Retired, Secret, Suggest, TokenCommand, TokenSources

#: The GitHub CLI's token variables, in its order.
_TOKEN_ENV = ("GH_TOKEN", "GITHUB_TOKEN")


class SweepSettings(BaseModel):
    """Settings for sweep corpus refresh behavior."""

    model_config = ConfigDict(frozen=True)

    max_age_seconds: int = Field(
        default=3600,
        ge=0,
        description=(
            "`sweep` and `cache sync` refresh cached repos older than this that "
            "GitHub reports as pushed since."
        ),
    )
    parallel: int = Field(
        default=12,
        ge=1,
        description="Default `sweep --parallel` and `cache sync --parallel` Git workers.",
    )


class InventorySettings(BaseModel):
    """The orgs and teams whose repos github lists for workspace (its ``RepoSource``)."""

    model_config = ConfigDict(frozen=True)

    orgs: list[str] = Field(
        default_factory=list,
        description=(
            "Orgs whose repositories github lists for workspace (repo names, the "
            "picker). With no orgs or teams, `github.default_org`."
        ),
    )
    teams: list[str] = Field(
        default_factory=list,
        description=(
            "Teams (`ORG/SLUG`, or `SLUG` in the one inventory org, else in "
            "`github.default_org`) whose repositories github lists for workspace."
        ),
    )


_INVENTORY_NOTE = (
    "deleted in 11.0; workspace keeps the repos github lists for "
    "workspace.caches.repo_source_repos.max_age_seconds, 6 hours by default "
    "(ctrl-r in its picker refreshes them)"
)


class GithubSettings(BaseModel):
    """GitHub API settings."""

    # Kept beside the Secret mark until 12.0: GithubSettings is api surface.
    token_sources: ClassVar[TokenSources] = TokenSources(env=_TOKEN_ENV)
    retired_keys: ClassVar[Mapping[str, str | Retired]] = {
        "corpus_path": "cache_dir",
        "cache_dir": Retired(note="deleted in 11.0; the repo store lives under git.store_dir"),
        "sweep.sync_concurrency": "sweep.parallel",
        "inventory.path": Retired(note=_INVENTORY_NOTE),
        "inventory.max_age_seconds": Retired(note=_INVENTORY_NOTE),
    }

    model_config = ConfigDict(frozen=True)

    base_url: str = Field(
        default="https://api.github.com",
        description="GitHub API URL. GitHub Enterprise Server uses `https://HOST/api/v3`.",
    )
    token: Annotated[
        SecretStr | None,
        NeedsSetup,
        Secret(
            env=_TOKEN_ENV,
            suggest=Suggest(("gh", "auth", "token"), note="after `gh auth login`"),
        ),
    ] = Field(
        default=None,
        description=(
            "GitHub token for API calls and Git fetches. Falls back to "
            "`token_command`, then `GH_TOKEN`, then `GITHUB_TOKEN`."
        ),
    )
    # Declared here until 12.0 (GithubSettings is api surface); core adds it for other plugins.
    token_command: TokenCommand = Field(
        default=None,
        description=(
            "Command (argv list, no shell) that prints the token; used when "
            "`github.token` is unset."
        ),
    )
    default_org: str | None = Field(
        default=None,
        description=(
            "Org scope for `repos list`, `search` (repos, code, issues), `sweep`, "
            "`cache sync` and `cache prune` when no scope flag is given. Without "
            "it, search uses `@me`."
        ),
    )
    git_protocol: Literal["https", "ssh"] = Field(
        default="https",
        description=(
            "How `sweep`, `cache sync`, workspace and `ansible source refresh` "
            "fetch repos on the GitHub host: `https` or `ssh` "
            "(`git@HOST:OWNER/NAME.git`, with your own SSH keys)."
        ),
    )
    sweep: SweepSettings = Field(default_factory=SweepSettings)
    inventory: InventorySettings = Field(default_factory=InventorySettings)
