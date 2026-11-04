"""``GithubHost``: GitHub fills the git plugin's ``GitHost`` contract for its own host.

The repo store asks it, never github's code: a fetch of a URL on the Git host
of ``github.server.url`` (``github.com``, or the GitHub Enterprise host) gets
the GitHub token as an ``x-access-token`` credential with the server's TLS
setting. A URL on any other host gets nothing from GitHub; the proxy is
untaped's, which git asks core for.

``probe_refs`` reads many repositories' refs in batched GraphQL queries,
peeling annotated tags up to two levels (as ``ls-remote`` peels them), and
says the budget is spent once it is below ``github.ref_probe.rate_limit_floor``.
"""

from __future__ import annotations

from urllib.parse import urlparse

from pydantic import SecretStr

from untaped.sdk import ErrorCategory, Failure, UntapedError
from untaped.sdk.contracts import Provider
from untaped_git.api import Credential, GitHost, ProbedRepo, RefProbe, repo_url_parts
from untaped_github.domain.hosts import github_web_host
from untaped_github.domain.models import BatchRepoRefsFailure
from untaped_github.errors import GITHUB
from untaped_github.server import github_api
from untaped_github.settings import TOKEN, GithubSettings

#: The username GitHub expects beside a token in HTTPS Git requests.
TOKEN_USERNAME = "x-access-token"


class GithubHost(GitHost, Provider[GithubSettings]):
    """GitHub's credentials for URLs on the Git host of ``github.server.url``."""

    def home(self) -> str | None:
        return github_web_host(self.settings.api_url)

    def credential(self, url: str) -> Credential | None:
        settings = self.settings
        if not _on_host(url, self.home()):
            return None
        # The API's token also serves its Git host, which is another origin.
        found = TOKEN.find(settings.server)
        token = "" if found is None else found.get_secret_value().strip()
        if not token:
            return None
        verify = settings.server.verify_tls
        if verify is True:  # untaped's trust: untaped.http.ca_bundle_path, when one is set
            verify = self.ctx.http.trust
        return Credential(username=TOKEN_USERNAME, password=SecretStr(token), verify_tls=verify)

    def probe_refs(
        self, urls: list[str], *, kinds: list[str], default_branch_only: bool, parallel: int
    ) -> RefProbe:
        named: dict[str, str] = {}  # owner/name, folded → the URL asked
        rows: list[ProbedRepo] = []
        for url in urls:
            host, segments = repo_url_parts(url)
            if host != self.home() or len(segments) != 2:
                rows.append(_failed(url, f"{url} is not a repository on {self.home()}", "invalid"))
            else:
                named.setdefault("/".join(segments).lower(), url)
        if not named:
            return RefProbe(repos=rows)
        api = github_api(self.settings)
        names = list(named)
        result = (
            api.batch_default_branch_refs(names, concurrency=parallel)
            if default_branch_only
            else api.batch_repo_refs(names, kinds=kinds, concurrency=parallel)
        )
        for repo in result.repos:
            rows.append(
                ProbedRepo(
                    url=named[repo.full_name.lower()],
                    refs={f"refs/{ref.kind}/{ref.name}": ref.sha for ref in repo.refs},
                    default_branch=repo.default_branch,
                )
            )
        rows.extend(_failed(named[name.lower()], _MISSING, "not_found") for name in result.missing)
        rows.extend(_chunk_failed(named[f.full_name.lower()], f) for f in result.failures)
        remaining = result.rate_limit_remaining
        floor = self.settings.ref_probe.rate_limit_floor
        return RefProbe(
            repos=rows,
            remaining=remaining,
            reset_at=result.rate_limit_reset_at,
            exhausted=remaining is not None and remaining < floor,
        )


_MISSING = "repository not found or inaccessible on GitHub"


def _failed(url: str, message: str, category: str) -> ProbedRepo:
    error = UntapedError(message, category=category, system=GITHUB)
    return ProbedRepo(url=url, failure=Failure.from_exception(error))


def _chunk_failed(url: str, failure: BatchRepoRefsFailure) -> ProbedRepo:
    """A repo whose request failed: never ``not_found``, which only GitHub's own answer says.

    A 404 on the GraphQL endpoint (a proxy, say) says nothing of the repo, so
    git still reads it with ``ls-remote``.
    """
    category = ErrorCategory.FAILED if failure.category == ErrorCategory.NOT_FOUND else None
    return _failed(url, f"batch request failed: {failure.reason}", category or failure.category)


def _on_host(url: str, host: str | None) -> bool:
    try:
        hostname = urlparse(url).hostname
    except ValueError:
        return False
    return host is not None and hostname is not None and hostname.lower() == host
