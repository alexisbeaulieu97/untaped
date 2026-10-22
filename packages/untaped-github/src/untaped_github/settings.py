"""Settings for the GitHub plugin."""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from datetime import timedelta
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, model_validator

from untaped.sdk import (  # type: ignore[deprecated]
    Cache,
    Credential,
    CredentialKey,
    Renamed,
    Retired,
    Suggest,
    TlsVerify,
    TokenCommand,
    TokenSources,
    deprecated,
    deprecated_behaviour,
)

#: The GitHub CLI's token variables, in its order.
_TOKEN_ENV = ("GH_TOKEN", "GITHUB_TOKEN")

#: github's default API URL: github.com's.
DEFAULT_URL = "https://api.github.com"

#: ``GithubSettings(base_url=...)`` and ``.base_url``, 11.0's spelling of ``server.url``.
BASE_URL_ALIAS = deprecated_behaviour(
    "base-url-alias", removed_in=12, replacement="GithubSettings.server.url"
)

with warnings.catch_warnings(action="ignore", category=DeprecationWarning):
    # 11.0 api surface, deprecated with TokenSources; GH names the variables now.
    _TOKEN_SOURCES = TokenSources(env=_TOKEN_ENV)


#: Each repo's stored copy the sweep and ``cache sync`` read, keyed by its store repo's
#: path. Its data is the store repo and its ``untaped-github.json``, shared by every
#: profile; the cache keeps when each copy was last checked. Setting:
#: ``github.caches.sweep.max_age_seconds``.
SWEEP = Cache(
    "github",
    "sweep",
    label="repo data",
    max_age=timedelta(hours=1),
    renamed_from=("sweep.max_age_seconds",),
    removed_in=12,
    split="none",
)


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

    # 11.0 api surface until 12.0; GH declares the variables.
    token_sources: ClassVar[TokenSources] = _TOKEN_SOURCES
    renamed_keys: ClassVar[Mapping[str, Renamed]] = {
        "base_url": Renamed("server.url", removed_in=12)
    }
    retired_keys: ClassVar[Mapping[str, str | Retired]] = {
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
    # 11.0 api surface until 12.0: 11.0's fallbacks still fill `token` from
    # `token_command` and GH's variables; a stored login is read with `GH`.
    token: Annotated[
        SecretStr | None,
        deprecated(removed_in=12, replacement="untaped auth login github"),
    ] = Field(
        default=None,
        description=(
            "GitHub token for API calls and Git fetches; deprecated, stored with "
            "`untaped auth login github`."
        ),
    )
    token_command: Annotated[
        TokenCommand,
        deprecated(removed_in=12, replacement="untaped auth login github --token-command"),
    ] = Field(
        default=None,
        description=(
            "Command (argv list, no shell) that prints the token; deprecated, stored "
            "with `untaped auth login github --token-command`."
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

    @model_validator(mode="before")
    @classmethod
    def _base_url_keyword(cls, data: Any) -> Any:
        """``GithubSettings(base_url=...)``, 11.0's spelling, read as ``server.url``."""
        if not isinstance(data, dict) or "base_url" not in data:
            return data
        BASE_URL_ALIAS.warn("GithubSettings(base_url=...)", stacklevel=4)
        data = dict(data)
        url = data.pop("base_url")
        server = data.get("server")
        server = dict(server) if isinstance(server, Mapping) else {}
        server.setdefault("url", url)
        return {**data, "server": server}

    @property
    def api_url(self) -> str:
        """The API URL as text, without the trailing slash of a bare host."""
        return api_text(self.server.url)

    @property
    def base_url(self) -> str:
        """11.0's name for :attr:`api_url` (:data:`BASE_URL_ALIAS`)."""
        BASE_URL_ALIAS.warn("GithubSettings.base_url", stacklevel=3)
        return self.api_url


def api_text(url: HttpUrl) -> str:
    """``url`` as text, without the ``/`` pydantic adds to a bare host."""
    text = str(url)
    return text[:-1] if text.endswith("/") and (url.path or "/") == "/" else text


#: The token for the GitHub API and its Git host, filed per server by
#: ``untaped auth login github``; ``gh``'s variables are tried last.
GH = Credential(
    "github",
    "token",
    key=GithubServer,
    env=_TOKEN_ENV,
    suggest=Suggest(("gh", "auth", "token"), note="after `gh auth login`"),
)
