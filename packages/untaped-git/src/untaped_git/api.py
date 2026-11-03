"""The git plugin's declared public module: the only untaped_git code other plugins import.

``RepoStore`` is the repo store (one blobless, full-history bare repo per URL,
shared by plugins, each in its own ref namespace); ``GitHost`` is the
contract a forge plugin fills to supply credentials and a proxy for its host;
``RepoSource`` is the contract plugins fill to list the repos a person can
name, issuing ``Repo`` records (and ``RepoSelector`` records from a
provider's own selector), and ``RepoFinder`` resolves a typed name or
URL to one of them (``typed_repo`` names a URL with no provider asked);
``ls_remote`` and ``default_branch`` query a remote with those credentials,
``probe_refs`` reads many remotes' refs through their host's probe (``GitHost.probe_refs``)
or ``ls-remote``,
and ``clone`` makes a working copy with them that its owner keeps;
``check_names`` says why a branch or tag name is one the store refuses.
``run_git`` runs any other git command the hardened way (no prompts, secrets
kept out of argv, failures as :class:`GitCommandError`), and
``git_toplevel`` finds a work tree's root.
The closed :data:`__all__` keeps the boundary explicit.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from untaped.sdk import experimental
from untaped_git import remote
from untaped_git.delta import RefDelta, RefMove
from untaped_git.hosts import Credential, GitHost, HostAuth, resolve_host
from untaped_git.namespace import check_names
from untaped_git.probe import HostBudget, ProbedRepo, ProbeResult, RefProbe, probe_refs
from untaped_git.records import TreeEntry
from untaped_git.release import Released, Removed
from untaped_git.repo_finder import RepoFinder
from untaped_git.repos import (
    Repo,
    RepoName,
    RepoSelector,
    RepoSource,
    looks_like_url,
    typed_repo,
)
from untaped_git.run import GitCommandError, GitResult, git_toplevel, run_git
from untaped_git.settings import store_root
from untaped_git.store import RepoStore
from untaped_git.store_prefetch import Prefetched
from untaped_git.url import GitUrl, repo_url_parts, store_key, validate_git_url

__all__ = [
    "Credential",
    "GitCommandError",
    "GitHost",
    "GitResult",
    "GitUrl",
    "HostAuth",
    "HostBudget",
    "Prefetched",
    "ProbeResult",
    "ProbedRepo",
    "RefDelta",
    "RefMove",
    "RefProbe",
    "Released",
    "Removed",
    "Repo",
    "RepoFinder",
    "RepoName",
    "RepoSelector",
    "RepoSource",
    "RepoStore",
    "TreeEntry",
    "check_names",
    "clone",
    "default_branch",
    "git_toplevel",
    "looks_like_url",
    "ls_remote",
    "probe_refs",
    "repo_url_parts",
    "run_git",
    "store_key",
    "store_root",
    "typed_repo",
    "validate_git_url",
]


def ls_remote(url: str, patterns: Sequence[str] = ()) -> dict[str, str]:
    """``ref → commit oid`` of ``url``'s refs matching ``patterns``, with untaped's credentials.

    ``HEAD`` included (with no patterns, or when one asks for it); an annotated
    tag maps to its commit.
    """
    return remote.ls_remote(url, patterns, root=store_root(), auth=resolve_host)


def default_branch(url: str) -> str | None:
    """The branch ``url``'s ``HEAD`` points at, or ``None`` (``ls-remote --symref``)."""
    return remote.default_branch(url, root=store_root(), auth=resolve_host)


@experimental
def clone(url: str, dest: Path, *, branch: str | None = None) -> None:
    """Clone ``url`` into ``dest``, a copy the user owns afterwards, with untaped's credentials.

    ``dest`` is absent or an empty folder, relative to the working directory;
    ``branch`` (a branch, or a tag for a detached copy) is checked out, else
    the remote's default branch. The credential of whoever fills ``GitHost``
    for the host and untaped's proxy reach this clone only: no credential
    helper or header is left in its config, so later fetches are the owner's
    own. An ssh URL asks no provider for a credential (its keys are the
    user's). A failure is an ``UntapedError`` carrying git's category. For
    untaped's own copy of a repository, use :class:`RepoStore` instead.
    """
    remote.clone(url, dest, branch=branch, root=store_root(), auth=resolve_host)
