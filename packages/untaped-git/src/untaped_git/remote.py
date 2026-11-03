"""Remote commands that need no store repo: ``ls-remote`` and ``clone`` with untaped's credentials.

Each runs from the store root with discovery stopped there, so a repository
around the process's working directory is never read, and asks
:class:`~untaped_git.hosts.GitHost` for the URL's credentials; core gives its
proxy. The credential and proxy reach git at command scope only, so a clone
keeps neither.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from pathlib import Path

from untaped.sdk import System, attribution
from untaped_git.errors import GitError
from untaped_git.host_auth import host_config
from untaped_git.hosts import HostAuth
from untaped_git.run import GitCommandError, run_git
from untaped_git.store_fetch import parse_symref, record_default_branch
from untaped_git.store_git import ATTEMPTS, SLOW_TIMEOUT, TIMEOUT
from untaped_git.url import store_key


def ls_remote(
    url: str,
    patterns: Sequence[str],
    *,
    root: Path,
    auth: Callable[[str], HostAuth | None],
) -> dict[str, str]:
    """``ref → commit oid`` of ``url``'s refs matching ``patterns`` (all refs when empty).

    ``HEAD`` is included (with no patterns, or when one asks for it), and an
    annotated tag maps to the commit it points at, not to the tag object.
    """
    # An exact pattern matches no ``<tag>^{}`` line: ask for each peeled line by name.
    peeling = [f"{pattern}^{{}}" for pattern in patterns if not pattern.endswith("^{}")]
    result = _ls_remote(url, ["--", url, *patterns, *peeling], root=root, auth=auth)
    refs: dict[str, str] = {}
    peeled: dict[str, str] = {}
    for line in result.splitlines():
        oid, _, ref = line.partition("\t")
        if ref.endswith("^{}"):
            peeled[ref.removesuffix("^{}")] = oid
        elif ref:
            refs[ref] = oid
    return refs | {ref: oid for ref, oid in peeled.items() if ref in refs}


def default_branch(url: str, *, root: Path, auth: Callable[[str], HostAuth | None]) -> str | None:
    """The branch ``url``'s ``HEAD`` points at (``ls-remote --symref``), or ``None``.

    The answer is recorded in ``url``'s store repo when it exists, so its next
    fetch points ``origin/HEAD`` without asking the remote again.
    """
    branch = parse_symref(_ls_remote(url, ["--symref", "--", url, "HEAD"], root=root, auth=auth))
    if branch is not None:
        record_default_branch(root.joinpath(*store_key(url)), branch)
    return branch


def clone(
    url: str,
    dest: Path,
    *,
    branch: str | None,
    root: Path,
    auth: Callable[[str], HostAuth | None],
) -> None:
    """Clone ``url`` into ``dest`` (absent or an empty folder), on ``branch`` or the remote's HEAD.

    ``dest`` is made absolute first: git runs from ``root``, not from the
    caller's working directory, and creates the missing folders itself.

    A full clone: the working copy is its owner's afterwards, and a partial one
    would fetch missing blobs later with the owner's credentials alone. The
    credential and proxy reach this one command; the clone's config keeps
    neither, nor a credential helper, so its later fetches are the owner's own.
    """
    target = os.path.abspath(dest)
    picked = ["--branch", branch] if branch is not None else []
    _remote(
        url,
        ["clone", "--quiet", *picked, "--", url, target],
        root=root,
        auth=auth,
        timeout=SLOW_TIMEOUT,
    )


def _ls_remote(
    url: str, args: Sequence[str], *, root: Path, auth: Callable[[str], HostAuth | None]
) -> str:
    return _remote(url, ["ls-remote", *args], root=root, auth=auth, timeout=TIMEOUT)


def _remote(
    url: str,
    argv: Sequence[str],
    *,
    root: Path,
    auth: Callable[[str], HostAuth | None],
    timeout: float,
) -> str:
    """Run ``argv`` from ``root`` with ``url``'s credential and proxy; its stdout."""
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise GitError(
            f"could not create repo store directory {root}: {exc.strerror or exc}",
            system=System.LOCAL,
        ) from exc
    secret, config = host_config(url, auth(url))
    try:
        result = run_git(
            argv,
            cwd=root,
            timeout=timeout,
            capture=True,
            auth_config=secret,
            ceiling=True,
            retry_transient=True,
            attempts=ATTEMPTS,
            config=config,
        )
    except GitCommandError as exc:
        raise GitError(str(exc), **attribution(exc)) from exc
    return result.text
