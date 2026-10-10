"""CLI composition root: the GitHub client and the corpus built from settings."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from untaped.sdk import app_context
from untaped_github.domain import github_web_host
from untaped_github.domain.errors import github_failures
from untaped_github.settings import GithubSettings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from untaped.sdk import UiContext
    from untaped_github.infrastructure import GithubClient
    from untaped_github.infrastructure.git_corpus import GitCorpusCache


def open_corpus(settings: GithubSettings) -> GitCorpusCache:
    """The local corpus in the repo store, fetching over ``github.git_protocol``."""
    from untaped_github.infrastructure.git_corpus import GitCorpusCache  # noqa: PLC0415

    return GitCorpusCache(
        web_host=github_web_host(settings.base_url), protocol=settings.git_protocol
    )


@contextmanager
def open_client() -> Iterator[tuple[GithubClient, UiContext]]:
    """Yield a :class:`GithubClient` and themed UI; a rejected token (401) gets a hint."""
    from untaped_github.infrastructure import GithubClient  # noqa: PLC0415

    ctx = app_context()
    # strict=False: a misconfigured theme must not fail an otherwise-valid
    # search/whoami. Progress is auxiliary feedback; it falls back to the
    # default theme rather than raising on the data path (e.g. --format raw).
    ui = ctx.ui(strict=False)
    with (
        GithubClient(ctx.section("github", GithubSettings), http=ctx.http) as client,
        github_failures(),
    ):
        yield client, ui
