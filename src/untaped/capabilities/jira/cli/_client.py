"""Shared CLI composition root for opening a Jira client."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from untaped.api import app_context
from untaped.capabilities.jira.settings import JiraSettings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from untaped.api import UiContext
    from untaped.capabilities.jira.infrastructure import JiraClient


@contextmanager
def open_client() -> Iterator[tuple[JiraClient, UiContext]]:
    """Build a context-managed Jira client and themed UI from active settings.

    The single tool context yields both the client and the themed
    :class:`UiContext` so commands can report progress without resolving
    settings twice. The UI is built with ``strict=False`` so a misconfigured
    theme degrades to the default theme rather than failing an otherwise-valid
    command on the data path (e.g. ``--format raw``).
    """

    from untaped.capabilities.jira.infrastructure import JiraClient  # noqa: PLC0415

    ctx = app_context()
    ui = ctx.ui(strict=False)
    with JiraClient(ctx.section("jira", JiraSettings), http=ctx.http) as client:
        yield client, ui


def current_jira_settings() -> JiraSettings:
    """Read active Jira settings without opening an HTTP client."""

    return app_context().section("jira", JiraSettings)
