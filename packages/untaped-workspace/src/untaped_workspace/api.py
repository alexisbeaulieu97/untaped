"""What another plugin may import from workspace: the repo record and the ``RepoSource`` contract.

Workspace never knows where a repo comes from: every installed plugin that
fills :class:`RepoSource` (github, a third-party gitlab…) answers, and
workspace picks among their answers by name (``untaped plugin rank`` orders
providers that list the same repo). This is the only workspace module a
provider imports.
"""

from __future__ import annotations

from abc import abstractmethod
from datetime import timedelta

from untaped.contracts import Contract, Record, bridge, cached, listing
from untaped.sdk import experimental
from untaped_workspace.domain.repo import Repo, RepoName

__all__ = ["Repo", "RepoName", "RepoSource"]


@experimental
class RepoSource[T: Record = Repo](Contract):
    """Where repos come from. ``T`` is the provider's own record type; it defaults to Repo."""

    @bridge
    def to_repo(self, item: T) -> Repo:
        """Turn the provider's own record into a Repo (only when ``T`` is not Repo)."""
        raise NotImplementedError

    @listing
    @cached(max_age=timedelta(hours=6))
    @abstractmethod
    def repos(self) -> list[Repo]:
        """Every repo the provider can list in the active profile."""
