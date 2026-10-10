"""The git plugin's declared public module: the only untaped_git code other plugins import.

``RepoStore`` is the repo store (one blobless, full-history bare repo per URL,
shared by plugins, each in its own ref namespace); ``GitHost`` is the
contract a forge plugin fills to supply credentials and a proxy for its host;
``ls_remote`` and ``default_branch`` query a remote with those credentials;
``adopt`` moves a repository an older untaped version left into the store
(``setup migrate-dirs`` rows call it).
The closed :data:`__all__` keeps the boundary explicit.
"""

from __future__ import annotations

import os
from collections.abc import Collection, Sequence
from pathlib import Path

from untaped.sdk import list_caches
from untaped_git.domain.delta import RefDelta, RefMove
from untaped_git.domain.hosts import Credential, GitHost, HostAuth, resolve_host
from untaped_git.domain.records import TreeEntry
from untaped_git.domain.release import Released, Removed
from untaped_git.domain.url import GitUrl, store_key, validate_git_url
from untaped_git.infrastructure import remote
from untaped_git.infrastructure.adopt import Adopted, adopt, remove_if_emptied
from untaped_git.infrastructure.repo_files import config_value
from untaped_git.infrastructure.store import Prefetched, RepoStore
from untaped_git.settings import git_settings

__all__ = [
    "Adopted",
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
    "adopt",
    "bare_repos",
    "default_branch",
    "ls_remote",
    "overlaps_store",
    "remove_if_emptied",
    "store_key",
    "store_root",
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


def store_root() -> Path:
    """The repo store's directory (``git.store_dir``, ``~`` expanded)."""
    return git_settings().store_dir.expanduser()


def overlaps_store(path: Path) -> bool:
    """Whether ``path`` is the repo store's directory, lies inside it or holds it.

    An older version's directory that does is never moved or deleted whole:
    a custom root configured to the same place as ``git.store_dir``.
    """
    real, root = Path(os.path.realpath(path.expanduser())), Path(os.path.realpath(store_root()))
    return real == root or real.is_relative_to(root) or root.is_relative_to(real)


def bare_repos(
    root: Path, *, skip: Collection[str] = (), workspace_layout: bool | None = None
) -> list[Path]:
    """Every ``*.git`` directory under ``root``, sorted, without running git.

    For an older version's cache root, before :func:`adopt`: never descends
    into a repository, skips symlinks and, at the top level, hidden
    directories and the names in ``skip``. ``workspace_layout`` keeps only the
    repos 10.x workspace marked ``untaped.layout`` (``True``) or only the
    others (``False``).
    """
    repos = list_caches(root, skip=skip)
    if workspace_layout is None:
        return repos
    return [
        repo
        for repo in repos
        if bool(config_value(repo / "config", "untaped", "layout")) is workspace_layout
    ]
