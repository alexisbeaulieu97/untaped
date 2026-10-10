"""Remote queries that need no store repo: ``ls-remote`` with untaped's credentials.

Both run from the store root with discovery stopped there, so a repository
around the process's working directory is never read, and ask
:class:`~untaped_git.domain.hosts.GitHost` for the URL's credentials and proxy.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from untaped.sdk import GitCommandError, attribution, run_git
from untaped_git.domain.hosts import HostAuth
from untaped_git.domain.url import https_origin, store_key
from untaped_git.errors import GitError
from untaped_git.infrastructure.store import (
    ATTEMPTS,
    TIMEOUT,
    credential_config,
    parse_symref,
    record_default_branch,
)


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
    result = _ls_remote(url, ["--", url, *patterns], root=root, auth=auth)
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


def _ls_remote(
    url: str, args: Sequence[str], *, root: Path, auth: Callable[[str], HostAuth | None]
) -> str:
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise GitError(
            f"could not create repo store directory {root}: {exc.strerror or exc}", system="local"
        ) from exc
    host = auth(url)
    origin = https_origin(url)
    secret: dict[str, str] = {}
    config: dict[str, str] = {}
    if host is not None and origin is not None:
        if host.credential is not None:
            secret = credential_config(origin, host.credential)
        if host.proxy:
            config[f"http.{origin}/.proxy"] = host.proxy
    try:
        result = run_git(
            ["ls-remote", *args],
            cwd=root,
            timeout=TIMEOUT,
            capture=True,
            secret_config=secret,
            ceiling=True,
            retry_transient=True,
            attempts=ATTEMPTS,
            config=config,
        )
    except GitCommandError as exc:
        raise GitError(str(exc), **attribution(exc)) from exc
    return result.text
