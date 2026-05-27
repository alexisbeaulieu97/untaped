"""Shared CLI composition-root helper for building :class:`GithubClient`.

Both ``whoami`` and ``search`` go through here so adding a new top-level
command is a one-line composition-root call.
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
    from untaped_github.infrastructure import GithubClient  # noqa: PLC0415

    settings = get_settings()
    return GithubClient(settings.github, http=settings.http)
