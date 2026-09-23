"""Canonical identity resolution for Ansible dependency declarations."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from untaped.capabilities.ansible.domain.models import DependencyDeclaration, ResolvedDependency

DEFAULT_GITHUB_HOST = "github.com"
_REPO = r"(?P<repo>[^/\s]+/[^/\s]+?)"
_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class IdentityResolver:
    """Resolve dependency declarations to canonical GitHub ``owner/repo`` ids.

    ``github.com`` URLs always resolve; ``github_host`` additionally accepts
    URLs on a configured GitHub Enterprise host.
    """

    def __init__(
        self,
        aliases: dict[str, str] | None = None,
        *,
        github_host: str | None = None,
    ) -> None:
        self._aliases = aliases or {}
        hosts = dict.fromkeys(host.lower() for host in (DEFAULT_GITHUB_HOST, github_host) if host)
        self._patterns = tuple(pattern for host in hosts for pattern in _url_patterns(host))

    def resolve(self, declaration: DependencyDeclaration) -> ResolvedDependency:
        key = declaration.src or declaration.name
        repo = (
            self._aliases.get(key)
            or self._aliases.get(declaration.name)
            or self._repo_from_source(key)
        )
        if repo is not None:
            return ResolvedDependency(declaration=declaration, repo=repo)
        return ResolvedDependency(declaration=declaration, unresolved=key)

    def _repo_from_source(self, source: str) -> str | None:
        value = source.strip()
        if _OWNER_REPO_RE.fullmatch(value):
            return value if _is_repo_id(value) else None
        for pattern in self._patterns:
            match = pattern.fullmatch(value)
            if match is not None:
                repo = match.group("repo").removesuffix(".git")
                return repo if _is_repo_id(repo) else None
        return None


def repo_key(repo: str) -> str:
    """Case-insensitive matching key for a GitHub ``owner/repo`` id."""
    return repo.lower()


def github_web_host(base_url: str) -> str | None:
    """Derive the Git web host from a GitHub REST API base URL.

    ``https://api.github.com`` -> ``github.com``; GitHub Enterprise Server
    ``https://ghe.example.com/api/v3`` -> ``ghe.example.com``; data-residency
    ``https://api.acme.ghe.com`` -> ``acme.ghe.com``. A ``/api/v3`` base is
    served from the web host itself, so its host is kept even when it starts
    with ``api.`` (``https://api.corp.example.com/api/v3``).
    """
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    if parsed.path.rstrip("/").lower().endswith("/api/v3"):
        return host
    return host.removeprefix("api.")


def _url_patterns(host: str) -> tuple[re.Pattern[str], ...]:
    escaped = re.escape(host)
    return (
        re.compile(rf"^(?:git\+)?https://{escaped}/{_REPO}(?:\.git)?/?$", re.IGNORECASE),
        re.compile(rf"^git@{escaped}:{_REPO}(?:\.git)?$", re.IGNORECASE),
        re.compile(rf"^ssh://git@{escaped}/{_REPO}(?:\.git)?/?$", re.IGNORECASE),
    )


def _is_repo_id(value: str) -> bool:
    """Reject path-like sources such as ``./local`` or ``../roles/web``."""
    owner, _, name = value.partition("/")
    return bool(owner and name) and not owner.startswith(".") and name not in {".", ".."}
