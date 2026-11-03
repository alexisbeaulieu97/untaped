"""Canonical identity resolution for Ansible dependency declarations.

A node id is the repository's name as its provider lists it (git's
``RepoName``): ``owner/repo`` on GitHub, ``group/sub/project`` on GitLab. A
URL names a repository when its host is one the sources' repositories live
on; untaped knows no host of its own.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from pydantic import TypeAdapter, ValidationError

from untaped_ansible.domain.models import DependencyDeclaration, ResolvedDependency
from untaped_git.api import RepoName, repo_url_parts

_REPO_NAME: TypeAdapter[str] = TypeAdapter(RepoName)
#: What a segment of a repository name is made of, on GitHub and GitLab alike.
_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+")
#: A URL to one of these is a file to download, not a repository.
_ARCHIVES = (".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".tar", ".zip")


class IdentityResolver:
    """Resolve dependency declarations to repository names.

    A bare name with two segments or more resolves as itself; a URL resolves
    when its host is in ``hosts`` (the hosts of the sources' repository URLs).
    Aliases come first.
    """

    def __init__(
        self, aliases: Mapping[str, str] | None = None, *, hosts: Iterable[str] = ()
    ) -> None:
        self._aliases = dict(aliases or {})
        self._hosts = frozenset(host.lower() for host in hosts)

    def resolve(self, declaration: DependencyDeclaration) -> ResolvedDependency:
        key = declaration.src or declaration.name
        repo = self._aliases.get(key) or self._aliases.get(declaration.name) or self.repo_of(key)
        if repo is not None:
            return ResolvedDependency(declaration=declaration, repo=repo)
        return ResolvedDependency(declaration=declaration, unresolved=key)

    def repo_of(self, source: str) -> str | None:
        """The repository ``source`` names: a repository name, or a URL on one of the hosts."""
        value = source.strip()
        if ":" not in value:  # a URL has a scheme or is ``user@host:path``
            return repo_name(value)
        host, segments = repo_url_parts(value)
        if host is None or host not in self._hosts or "-" in segments:  # ``/-/``: a web page
            return None
        if value.lower().endswith(_ARCHIVES):
            return None
        return repo_name("/".join(segments))


def repo_name(value: str) -> str | None:
    """``value`` when it is a repository name (``owner/repo``, ``group/sub/project``).

    A name has two segments or more, each of letters, digits, ``_``, ``.``
    and ``-``. A path such as ``./local``, ``../roles`` or ``.hidden/role``,
    and a template such as ``{{ org }}/role``, is not one (``None``): the
    first segment never starts with a dot and no segment is ``.`` or ``..``
    (``acme/.github`` is a name).
    """
    try:
        _REPO_NAME.validate_python(value)
    except ValidationError:
        return None
    segments = value.split("/")
    if len(segments) < 2 or segments[0].startswith(".") or {".", ".."} & set(segments):
        return None
    if not all(_SEGMENT.fullmatch(segment) for segment in segments):
        return None
    return value


def url_hosts(urls: Iterable[str | None]) -> frozenset[str]:
    """The hosts of ``urls`` (lowercased); local paths and ``None`` add none."""
    return frozenset(host for url in urls if url for host in [repo_url_parts(url)[0]] if host)


def repo_key(repo: str) -> str:
    """Case-insensitive matching key for a repository name."""
    return repo.lower()
