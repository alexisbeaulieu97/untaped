"""Settings for the GitHub plugin."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from untaped.sdk import Cache, Credential, CredentialKey, Retired, Suggest, TlsVerify

#: The GitHub CLI's token variables, in its order.
_TOKEN_ENV = ("GH_TOKEN", "GITHUB_TOKEN")

#: github's default API URL: github.com's.
DEFAULT_URL = "https://api.github.com"

#: Each repo's stored copy the sweep and ``cache sync`` read, keyed by its store repo's
#: path. Its data is the store repo and its ``untaped-github.json``, shared by every
#: profile; the cache keeps when each copy was last checked. The plugin class holds
#: it as ``sweep``; setting: ``github.caches.sweep.max_age_seconds``.
SWEEP = Cache(label="repo data", max_age=timedelta(hours=1), split="none")


class SweepSettings(BaseModel):
    """Settings for sweep corpus refresh behavior."""

    model_config = ConfigDict(frozen=True)

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


_TOKEN_NOTE = "deleted in 12.0; store the token with `untaped auth login github`"

_INVENTORY_NOTE = (
    "deleted in 11.0; workspace keeps the repos github lists for "
    "workspace.caches.repo_source_repos.max_age_seconds, 6 hours by default "
    "(ctrl-r in its picker refreshes them)"
)


class GithubServer(CredentialKey):
    """The GitHub API a token is for: its URL, and how its certificate is checked."""

    url: HttpUrl = Field(
        default=HttpUrl(DEFAULT_URL),
        description="GitHub API URL. GitHub Enterprise Server uses `https://HOST/api/v3`.",
    )
    verify_tls: TlsVerify = Field(
        default=True,
        description=(
            "Check the server's certificate: `true` (untaped's trust), `false`, "
            "or a CA file for this server alone."
        ),
    )


class GithubSettings(BaseModel):
    """GitHub API settings."""

    #: Gone: read only to name a successor or say what to do instead.
    retired_keys: ClassVar[Mapping[str, str | Retired]] = {
        "base_url": "server.url",
        "token": Retired(note=_TOKEN_NOTE),
        "token_command": Retired(note=_TOKEN_NOTE),
        "sweep.max_age_seconds": Retired(
            note="deleted in 12.0; use github.caches.sweep.max_age_seconds"
        ),
        "corpus_path": "cache_dir",
        "cache_dir": Retired(note="deleted in 11.0; the repo store lives under git.store_dir"),
        "sweep.sync_concurrency": "sweep.parallel",
        "inventory.path": Retired(note=_INVENTORY_NOTE),
        "inventory.max_age_seconds": Retired(note=_INVENTORY_NOTE),
    }

    model_config = ConfigDict(frozen=True)

    server: GithubServer = Field(
        default_factory=GithubServer,
        description=(
            "The GitHub API: its `url` and `verify_tls`. Its token is stored with "
            "`untaped auth login github`."
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

    @property
    def api_url(self) -> str:
        """The API URL as text, without the trailing slash of a bare host."""
        return api_text(self.server.url)


def api_text(url: HttpUrl) -> str:
    """``url`` as text, without the ``/`` pydantic adds to a bare host."""
    text = str(url)
    return text[:-1] if text.endswith("/") and (url.path or "/") == "/" else text


#: The token for the GitHub API and its Git host, filed per server by
#: ``untaped auth login github``; ``gh``'s variables are tried last. The plugin
#: class holds it as ``token``.
TOKEN = Credential(
    key=GithubServer,
    env=_TOKEN_ENV,
    suggest=Suggest(("gh", "auth", "token"), note="after `gh auth login`"),
)
