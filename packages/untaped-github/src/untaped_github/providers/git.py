"""``GithubHost``: GitHub fills the git plugin's ``GitHost`` contract for its own host.

The repo store asks it, never github's code: a fetch of a URL on the Git host
of ``github.server.url`` (``github.com``, or the GitHub Enterprise host) gets
the GitHub token as an ``x-access-token`` credential with the server's TLS
setting, and the profile's
``untaped.http.proxy`` when one is set, so one setting proxies both GitHub's API and
its Git fetches. A URL on any other host gets nothing from GitHub.
"""

from __future__ import annotations

from urllib.parse import urlparse

from pydantic import SecretStr

from untaped.sdk import app_context, get_config_section
from untaped_git.api import Credential, GitHost
from untaped_github.domain.hosts import github_web_host
from untaped_github.settings import GH, GithubSettings

#: The username GitHub expects beside a token in HTTPS Git requests.
TOKEN_USERNAME = "x-access-token"


class GithubHost(GitHost):
    """GitHub's credentials and proxy for URLs on the Git host of ``github.server.url``."""

    def home(self) -> str | None:
        return github_web_host(_settings().api_url)

    def credential(self, url: str) -> Credential | None:
        settings = _settings()
        if not _on_host(url, github_web_host(settings.api_url)):
            return None
        # The API's token also serves its Git host, which is another origin.
        found = GH.find(settings.server)
        token = "" if found is None else found.get_secret_value().strip()
        if not token:
            return None
        verify = settings.server.verify_tls
        if verify is True:  # untaped's trust: root untaped.http.ca_bundle_path, when one is set
            verify = app_context().http.ca_bundle_path or True
        return Credential(username=TOKEN_USERNAME, password=SecretStr(token), verify_tls=verify)

    def proxy(self, url: str) -> str | None:
        if not _on_host(url, self.home()):
            return None
        return app_context().http.proxy or None


def _settings() -> GithubSettings:
    return get_config_section("github", GithubSettings)


def _on_host(url: str, host: str | None) -> bool:
    try:
        hostname = urlparse(url).hostname
    except ValueError:
        return False
    return host is not None and hostname is not None and hostname.lower() == host
