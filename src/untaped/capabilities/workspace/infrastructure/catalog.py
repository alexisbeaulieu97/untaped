"""Resolve repo identifiers to clone URLs through the GitHub inventory.

URLs and paths pass through untouched; ``owner/name`` and bare names are
looked up in the inventory ``untaped.capabilities.github.api`` exposes
(imported lazily so workspace startup stays free of github modules).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Literal

from untaped.capabilities.workspace.domain.models import ResolvedRepo
from untaped.capabilities.workspace.domain.naming import repo_identity
from untaped.capability_api import UntapedError, UsageError, not_found, q

if TYPE_CHECKING:
    from untaped.capabilities.github.api import RepositoryInventoryItem

_SCP = re.compile(r"^[\w.-]+@[\w.-]+:.+")
_REFRESH_HINT = (
    "check the name, or the github.inventory orgs/teams settings that scope the inventory"
)
_URL_HINT = "pass a full clone URL instead"


def _is_url_or_path(ident: str) -> bool:
    return (
        "://" in ident
        or ident.startswith(("/", "~"))
        or bool(_SCP.match(ident))
        or ident.endswith(".git")
    )


def _default_inventory() -> Sequence[RepositoryInventoryItem]:
    from untaped.capabilities.github.api import repo_inventory  # noqa: PLC0415

    return repo_inventory().repos


class GithubRepoCatalog:
    """``RepoCatalog`` backed by the GitHub repository inventory."""

    def __init__(
        self,
        *,
        protocol: Literal["https", "ssh"],
        inventory: Callable[[], Sequence[RepositoryInventoryItem]] | None = None,
    ) -> None:
        self._protocol = protocol
        self._inventory = inventory or _default_inventory

    def resolve(self, ident: str) -> ResolvedRepo:
        if _is_url_or_path(ident):
            owner, name = repo_identity(ident)
            return ResolvedRepo(url=ident, name=f"{owner}/{name}" if owner else name)
        try:
            items = self._inventory()
        except UntapedError as error:
            return self._without_inventory(ident, error)
        return self._from_item(self._match(ident, items))

    def _without_inventory(self, ident: str, error: UntapedError) -> ResolvedRepo:
        owner, _, name = ident.partition("/")
        if owner and name and "/" not in name:
            host = _web_host()
            if host:
                url = (
                    f"git@{host}:{owner}/{name}.git"
                    if self._protocol == "ssh"
                    else f"https://{host}/{owner}/{name}.git"
                )
                return ResolvedRepo(url=url, name=f"{owner}/{name}")
        if not error.hint:
            error.hint = _URL_HINT
        raise error

    def _match(
        self, ident: str, items: Sequence[RepositoryInventoryItem]
    ) -> RepositoryInventoryItem:
        wanted = ident.lower()
        if "/" in ident:
            matches = [item for item in items if item.full_name.lower() == wanted]
        else:
            matches = [
                item
                for item in items
                if wanted in {(item.name or "").lower(), item.full_name.rpartition("/")[2].lower()}
            ]
        if not matches:
            raise UsageError(not_found("repo", ident), hint=_REFRESH_HINT)
        if len(matches) > 1:
            names = ", ".join(sorted(item.full_name for item in matches))
            raise UsageError(f"repo name {q(ident)} is ambiguous: {names}", hint="pass owner/name")
        return matches[0]

    def _from_item(self, item: RepositoryInventoryItem) -> ResolvedRepo:
        url = (item.ssh_url if self._protocol == "ssh" else None) or item.clone_url
        if not url:
            raise UsageError(
                f"repo {q(item.full_name)} has no clone URL in the inventory",
                hint=_URL_HINT,
            )
        return ResolvedRepo(url=url, name=item.full_name, default_branch=item.default_branch)


def _web_host() -> str | None:
    from untaped.capabilities.github.api import (  # noqa: PLC0415
        github_settings,
        github_web_host,
    )

    return github_web_host(github_settings().base_url)
