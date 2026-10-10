"""The git plugin's declared public module: the only untaped_git code other plugins import.

``RepoStore`` is the repo store (one blobless, full-history bare repo per URL,
shared by plugins, each in its own ref namespace); ``GitHost`` is the
contract a forge plugin fills to supply credentials and a proxy for its host;
``ls_remote`` and ``default_branch`` query a remote with those credentials.
The closed :data:`__all__` keeps the boundary explicit.
"""

from __future__ import annotations

from collections.abc import Sequence

from untaped_git.domain.delta import RefDelta, RefMove
from untaped_git.domain.hosts import Credential, GitHost, HostAuth, resolve_host
from untaped_git.domain.records import TreeEntry
from untaped_git.domain.release import Released, Removed
from untaped_git.domain.url import GitUrl, store_key, validate_git_url
from untaped_git.infrastructure import remote
from untaped_git.infrastructure.store import Prefetched, RepoStore
from untaped_git.settings import git_settings

__all__ = [
    "Credential",
    "GitHost",
    "GitUrl",
    "HostAuth",
    "Prefetched",
    "RefDelta",
    "RefMove",
    "Released",
    "Removed",
    "RepoStore",
    "TreeEntry",
    "default_branch",
    "ls_remote",
    "store_key",
    "validate_git_url",
]


def ls_remote(url: str, patterns: Sequence[str] = ()) -> dict[str, str]:
    """``ref → commit oid`` of ``url``'s refs matching ``patterns``, with untaped's credentials.

    ``HEAD`` included (with no patterns, or when one asks for it); an annotated
    tag maps to its commit.
    """
    root = git_settings().store_dir.expanduser()
    return remote.ls_remote(url, patterns, root=root, auth=resolve_host)


def default_branch(url: str) -> str | None:
    """The branch ``url``'s ``HEAD`` points at, or ``None`` (``ls-remote --symref``)."""
    root = git_settings().store_dir.expanduser()
    return remote.default_branch(url, root=root, auth=resolve_host)
