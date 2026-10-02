"""CLI composition root: the GitHub client and corpus auth built from settings."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from typing import TYPE_CHECKING

from untaped.capabilities.github.domain.errors import github_failures
from untaped.capabilities.github.settings import GithubSettings
from untaped.sdk import app_context, git_auth_header

if TYPE_CHECKING:
    from collections.abc import Iterator

    from untaped.capabilities.github.infrastructure import GithubClient
    from untaped.sdk import UiContext


def corpus_auth_header(settings: GithubSettings) -> Callable[[], str | None]:
    """Return the Git auth-header supplier for corpus fetches (None without a token)."""
    token = settings.token.get_secret_value().strip() if settings.token is not None else ""
    return lambda: git_auth_header(token) if token else None


@contextmanager
def open_client() -> Iterator[tuple[GithubClient, UiContext]]:
    """Yield a :class:`GithubClient` and themed UI; a rejected token (401) gets a hint."""
    from untaped.capabilities.github.infrastructure import GithubClient  # noqa: PLC0415

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
