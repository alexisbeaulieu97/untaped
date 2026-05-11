"""Application-layer protocols (ports) for the GitHub bounded context."""

from __future__ import annotations

from typing import Any, Protocol


class GithubMeService(Protocol):
    """The authenticated-user fetch contract that ``WhoAmI`` depends on."""

    def me(self) -> dict[str, Any]: ...
