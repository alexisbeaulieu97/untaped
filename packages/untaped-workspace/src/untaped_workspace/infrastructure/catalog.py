"""Resolve repo identifiers to clone URLs through the GitHub inventory.

URLs and paths pass through untouched; ``owner/name`` and bare names are
looked up in the inventory ``untaped_github.api`` exposes
(imported lazily so workspace startup stays free of github modules).
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Literal

from untaped.sdk import UntapedError, UsageError, not_found, q
from untaped_workspace.domain.models import ResolvedRepo
from untaped_workspace.domain.naming import looks_like_url, repo_identity

if TYPE_CHECKING:
    from untaped_github.api import RepositoryInventoryItem

_SLUG = re.compile(r"[A-Za-z0-9][\w.-]*")
"""An owner or repo name a URL may be built from (so ``./x`` never becomes one)."""
_REFRESH_HINT = (
    "check the name, or the github.inventory orgs/teams settings that scope the inventory"
)
_URL_HINT = "pass a full clone URL instead"


def _default_inventory() -> Sequence[RepositoryInventoryItem]:
    from untaped_github.api import repo_inventory  # noqa: PLC0415

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
        self._items: Sequence[RepositoryInventoryItem] | UntapedError | None = None

    def resolve(self, ident: str) -> ResolvedRepo:
        if looks_like_url(ident):
            owner, name = repo_identity(ident)
            return ResolvedRepo(url=ident, name=f"{owner}/{name}" if owner else name)
        items = self._load()
        if isinstance(items, UntapedError):
            return self._without_inventory(ident, items)
        return self._from_item(self._match(ident, items))

    def _load(self) -> Sequence[RepositoryInventoryItem] | UntapedError:
        """The inventory (or why it is unavailable), read once per catalog."""
        if self._items is None:
            try:
                self._items = self._inventory()
            except UntapedError as error:
                self._items = error
        return self._items

    def _without_inventory(self, ident: str, error: UntapedError) -> ResolvedRepo:
        owner, _, name = ident.partition("/")
        if _SLUG.fullmatch(owner) and _SLUG.fullmatch(name):
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
            raise UsageError(_not_found(ident, items), hint=_REFRESH_HINT)
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


def _not_found(ident: str, items: Sequence[RepositoryInventoryItem]) -> str:
    """``repo not found: 'x'``, with the inventory's close matches when there are any."""
    by_key: dict[str, list[str]] = {}
    for item in items:
        key = item.full_name if "/" in ident else item.full_name.rpartition("/")[2]
        by_key.setdefault(key.lower(), []).append(item.full_name)
    close = difflib.get_close_matches(ident.lower(), list(by_key), n=3)
    suggestions = sorted({name for key in close for name in by_key[key]})
    message = not_found("repo", ident)
    if suggestions:
        message += f"; did you mean {', '.join(map(q, suggestions))}?"
    return message


def _web_host() -> str | None:
    from untaped_github.api import (  # noqa: PLC0415
        github_settings,
        github_web_host,
    )

    return github_web_host(github_settings().base_url)
