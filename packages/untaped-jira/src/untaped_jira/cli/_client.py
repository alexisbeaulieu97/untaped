"""Shared CLI composition root for opening a Jira client."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from untaped.sdk import app_context
from untaped_jira.settings import JiraSettings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from untaped.sdk import UiContext
    from untaped_jira.infrastructure import JiraClient


@contextmanager
def open_client() -> Iterator[tuple[JiraClient, UiContext]]:
    """Yield a Jira client and a UI whose unknown theme falls back to the default."""
    from untaped_jira.infrastructure import JiraClient  # noqa: PLC0415

    ctx = app_context()
    ui = ctx.ui(strict=False)
    with JiraClient(ctx.section("jira", JiraSettings), http=ctx.http) as client:
        yield client, ui


def current_jira_settings() -> JiraSettings:
    """Read active Jira settings without opening an HTTP client."""

    return app_context().section("jira", JiraSettings)
