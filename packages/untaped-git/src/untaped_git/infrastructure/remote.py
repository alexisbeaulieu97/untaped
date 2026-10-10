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
from untaped_git.domain.url import https_origin
from untaped_git.errors import GitError
from untaped_git.infrastructure.store import (
    ATTEMPTS,
    TIMEOUT,
    basic_header,
    parse_symref,
)


def ls_remote(
    url: str,
    patterns: Sequence[str],
    *,
    root: Path,
    auth: Callable[[str], HostAuth | None],
) -> dict[str, str]:
    """``ref → oid`` of ``url``'s refs matching ``patterns`` (all refs when empty)."""
    result = _ls_remote(url, ["--refs", url, *patterns], root=root, auth=auth)
    refs: dict[str, str] = {}
    for line in result.splitlines():
        oid, _, ref = line.partition("\t")
        if ref:
            refs[ref] = oid
    return refs


def default_branch(url: str, *, root: Path, auth: Callable[[str], HostAuth | None]) -> str | None:
    """The branch ``url``'s ``HEAD`` points at (``ls-remote --symref``), or ``None``."""
    return parse_symref(_ls_remote(url, ["--symref", url, "HEAD"], root=root, auth=auth))


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
    header = None
    config: dict[str, str] = {}
    if host is not None and origin is not None:
        if host.credential is not None:
            header = basic_header(host.credential.username, host.credential.password)
        if host.proxy:
            config[f"http.{origin}/.proxy"] = host.proxy
    try:
        result = run_git(
            ["ls-remote", *args],
            cwd=root,
            timeout=TIMEOUT,
            capture=True,
            auth_header=header,
            auth_url=url if header else None,
            ceiling=True,
            retry_transient=True,
            attempts=ATTEMPTS,
            config=config,
        )
    except GitCommandError as exc:
        raise GitError(str(exc), **attribution(exc)) from exc
    return result.text
