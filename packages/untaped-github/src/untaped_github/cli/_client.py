"""Shared CLI composition-root helper for building :class:`GithubClient`.

Both ``whoami`` and ``search`` need the same three-step recipe (read
``Settings``, bridge to ``GithubConfig``, instantiate the client). Living
in one place keeps the bridge symmetric with AWX's
``AwxContext.__init__`` and makes adding a new top-level command a
one-line composition-root call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from untaped_core import get_settings

if TYPE_CHECKING:
    from untaped_github.infrastructure import GithubClient


def open_client() -> GithubClient:
    """Build a :class:`GithubClient` from the cached :class:`Settings`.

    Deferred imports keep the workspace-wide rule about lazy imports on
    CLI startup paths satisfied (the GitHub client's transitive imports
    — httpx, pydantic models, etc. — would otherwise pay on every
    ``untaped --help``).
    """
    from untaped_github.infrastructure import GithubClient, GithubConfig  # noqa: PLC0415

    settings = get_settings()
    return GithubClient(GithubConfig.from_settings(settings), http=settings.http)
