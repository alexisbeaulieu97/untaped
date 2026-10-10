"""Workspace's checks on any provider of ``RepoSource``: ``untaped plugin check`` runs them.

A provider's own tests may call :func:`conformance` too, on a provider
composed with ``untaped.testing.compose_with``.
"""

from __future__ import annotations

from untaped_workspace.api import RepoSource

__all__ = ["conformance"]


def conformance(provider: RepoSource) -> None:
    """Fail unless every repo ``provider`` lists has a name of its own.

    Workspace finds a repo by name, so two repos one provider lists under one
    name could never be told apart.
    """
    names = [repo.name.lower() for repo in provider.repos()]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert not duplicates, f"repos share a name: {', '.join(duplicates)}"
