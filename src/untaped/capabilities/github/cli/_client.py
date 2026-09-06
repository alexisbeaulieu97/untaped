"""Shared CLI composition-root helper for building :class:`GithubClient`.

Both ``whoami`` and ``search`` go through here so adding a new top-level
command is a one-line composition-root call.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from untaped.api import app_context
from untaped.capabilities.github.settings import GithubSettings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from untaped.api import UiContext
    from untaped.capabilities.github.infrastructure import GithubClient


@contextmanager
def open_client() -> Iterator[tuple[GithubClient, UiContext]]:
    """Build a :class:`GithubClient` and themed UI from a one-shot context.

    ``app_context()`` resolves settings exactly once (honoring the root
    ``--profile`` selector applied by core) and hands back a frozen context;
    nothing leaks into ambient process state. The same context yields the
    themed :class:`UiContext` so commands can report progress without resolving
    settings a second time. Deferred imports keep the workspace-wide rule about
    lazy imports on CLI startup paths satisfied (the GitHub client's transitive
    imports — httpx, pydantic models, etc. — would otherwise pay on every
    ``untaped --help``).
    """
    from untaped.capabilities.github.infrastructure import GithubClient  # noqa: PLC0415

    ctx = app_context()
    # strict=False: a misconfigured theme must not fail an otherwise-valid
    # search/whoami. Progress is auxiliary feedback; it falls back to the
    # default theme rather than raising on the data path (e.g. --format raw).
    ui = ctx.ui(strict=False)
    with GithubClient(ctx.section("github", GithubSettings), http=ctx.http) as client:
        yield client, ui
