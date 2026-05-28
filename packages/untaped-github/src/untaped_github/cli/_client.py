"""Shared CLI composition-root helper for building :class:`GithubClient`.

Both ``whoami`` and ``search`` go through here so adding a new top-level
command is a one-line composition-root call. See
``untaped_awx.cli._context.AwxContext`` for the symmetric pattern in
the AWX package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from untaped import get_config_section, get_core_settings
from untaped_github.settings import GithubSettings

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

    settings = get_core_settings()
    return GithubClient(get_config_section("github", GithubSettings), http=settings.http)
