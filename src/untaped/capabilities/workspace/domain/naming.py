"""Naming rules: workspace names, repo identities and keys, directory names, branches."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from urllib.parse import urlparse

from untaped.capabilities.workspace.domain.models import RepoSpec
from untaped.capability_api import UsageError, q, safe_path_segment

_SCP = re.compile(r"^(?P<user>[^@]+)@(?P<host>[^:]+):(?P<path>.+)$")


def validate_workspace_name(name: str) -> str:
    """Return ``name`` if it is one safe path segment, else raise ``UsageError``."""
    if not name or name.startswith(".") or safe_path_segment(name) != name:
        raise UsageError(f"invalid workspace name {q(name)}: use letters, digits, '.', '_' or '-'")
    return name


def looks_like_url(ident: str) -> bool:
    """Whether ``ident`` is a URL (``scheme://``) or scp-style ``user@host:path``."""
    return "://" in ident or bool(_SCP.match(ident))


def repo_identity(url: str) -> tuple[str, str]:
    """``(owner, name)`` from a clone URL or path; owner is ``""`` when absent."""
    _, segments = _host_and_path(url)
    name = segments[-1] if segments else url
    owner = segments[-2] if len(segments) > 1 else ""
    return owner, name


def repo_key(url: str) -> tuple[str, ...]:
    """The path of ``url``'s bare cache under ``cache_dir``: one key per repo.

    ``<host>/<owner>/<name>.git``, so the https and ssh URLs of one repo
    share a key. A URL without a host (a local path, ``file://``) keys on
    a hash of the whole string. Every part is one safe path segment, so a
    hostile URL (``https://evil/../../tmp/pwn.git``) never escapes the cache.
    """
    host, segments = _host_and_path(url)
    if host is None or not segments:
        return ("_unknown", f"{hashlib.sha256(url.encode()).hexdigest()[:16]}.git")
    *parents, leaf = (safe_path_segment(part) for part in segments)
    return (safe_path_segment(host), *parents, f"{leaf}.git")


def _host_and_path(url: str) -> tuple[str | None, list[str]]:
    """``(host, path segments, the last without .git)`` of a URL, ``user@host:path`` or path.

    A plain path (or a ``file://`` URL) has no host.
    """
    match = _SCP.match(url)
    if "://" in url:
        parsed = urlparse(url)
        host, path = parsed.hostname, parsed.path or ""
    elif match:
        host, path = match.group("host"), match.group("path")
    else:
        host, path = None, url
    segments = [s for s in path.replace("\\", "/").split("/") if s]
    if segments:
        segments[-1] = segments[-1].removesuffix(".git")
    return host, segments


def assign_dirs(new: Sequence[tuple[str, str]], existing: Sequence[RepoSpec]) -> list[str]:
    """Directory names for ``new`` repos: the repo name, ``owner-name`` on a clash."""
    taken = {spec.dir for spec in existing}
    dirs: list[str] = []
    for owner, name in new:
        clash = name in taken
        candidate = safe_path_segment(f"{owner}-{name}" if clash and owner else name)
        while candidate in taken:
            candidate = f"{candidate}-{len(taken)}"
        taken.add(candidate)
        dirs.append(candidate)
    return dirs


def branch_for(template: str, workspace: str) -> str:
    """The branch a writable repo gets: ``template`` with ``{name}`` filled in."""
    return template.replace("{name}", workspace)
