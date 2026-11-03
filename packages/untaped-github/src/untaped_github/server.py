"""github's server API: how it tells github.com from GitHub Enterprise Server, and who serves each.

Commands reach GitHub through ``SERVER_API.port(settings)``, which core
serves with the adapter for the server's version (``docs/server-apis.md``).
The plugin class holds it as ``server_api``: the detection cache is
``github.server_api``, the doctor row ``github.server-api``, and
``github.server_version`` overrides detection.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from untaped.sdk import key
from untaped.sdk.network import ApiMismatch, HttpClient, Release, ServerApi
from untaped_github.application.ports import GithubApi
from untaped_github.infrastructure.rest_2022 import Rest2022
from untaped_github.settings import DEFAULT_URL, TOKEN, GithubSettings
from untaped_github.version import GithubVersion, Product


class ApiRoot(BaseModel):
    """GitHub's API root (``GET /``): the one field every GitHub answers it with."""

    model_config = ConfigDict(extra="ignore")

    current_user_url: str


def detect(settings: GithubSettings, client: HttpClient) -> GithubVersion:
    """github.com is known from its URL, with no request; otherwise reads GitHub's API root,
    `GET /`, and its `X-GitHub-Enterprise-Version` header.
    """
    if settings.api_url == DEFAULT_URL:
        return GithubVersion(Product.DOTCOM)
    # A body that isn't GitHub's API root (a web page) is "could not tell".
    with ApiMismatch.watch():
        response = client.get("/")
        ApiRoot.model_validate_json(response.content)
    header = response.headers.get("X-GitHub-Enterprise-Version")
    if header is None:  # GHE.com and other hosted API roots
        return GithubVersion(Product.DOTCOM)
    try:
        release = Release.parse(header)
    except ValueError as exc:
        answer = f"an X-GitHub-Enterprise-Version that is not a release ({header!r})"
        raise ApiMismatch(f"GET / answered {answer}", request="GET /", answer=answer) from exc
    return GithubVersion(Product.GHES, release)


def github_api(settings: GithubSettings) -> GithubApi:
    """github's port for ``settings``' server: the GitHub API, served for its version.

    Every call needs the token, so a missing one fails here, before any
    work, naming ``untaped auth login github``. Nothing is sent until a call;
    the run closes its connections when it ends, and ports built from one
    settings object share them.
    """
    TOKEN.get(settings.server)
    return SERVER_API.port(settings)


SERVER_API = ServerApi[GithubApi, GithubSettings, GithubVersion](
    server=key(GithubSettings).server,
    credential=TOKEN,
    detect=detect,
    adapters=(Rest2022,),
    override=key(GithubSettings).server_version,
)

__all__ = ["SERVER_API", "ApiRoot", "detect", "github_api"]
