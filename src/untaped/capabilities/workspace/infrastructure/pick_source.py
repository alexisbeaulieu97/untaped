"""The repo picker's catalog: the GitHub inventory plus repos already in the cache.

Inventory items come from ``github.api.repo_inventory`` (imported lazily, so
workspace startup stays free of github modules). Repos only in the bare-cache
directory are added so the picker also works offline or without GitHub
configured. Branch completion reads the local cache only, never the network.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Collection
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.capabilities.workspace.domain.naming import looks_like_url, repo_key
from untaped.capability_api import PickCatalog, PickItem, UntapedError

if TYPE_CHECKING:
    from untaped.capabilities.github.api import RepoInventory
    from untaped.capabilities.workspace.application.ports import GitWorktrees

_UNKNOWN = "_unknown"
"""Cache directory of repos whose URL has no host (see :func:`repo_key`)."""


def _default_inventory(refresh: bool | None) -> RepoInventory:
    from untaped.capabilities.github.api import repo_inventory  # noqa: PLC0415

    return repo_inventory(refresh=refresh)


def _age(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


class RepoPickSource:
    """Picker items from the inventory and the local repo cache, plus branch completion.

    ``exclude`` holds the cache identities (:func:`repo_key`) of repos the
    workspace already has; they are not offered.
    """

    def __init__(
        self,
        *,
        cache_dir: Path,
        git: GitWorktrees,
        inventory: Callable[[bool | None], RepoInventory] | None = None,
        exclude: Collection[tuple[str, ...]] = (),
    ) -> None:
        self._cache_dir = cache_dir.expanduser()
        self._git = git
        self._inventory = inventory or _default_inventory
        self._exclude = frozenset(exclude)
        self._urls: dict[str, str] = {}
        self._cached: dict[str, tuple[str, ...]] = {}
        self._branches: dict[str, list[str]] = {}

    def catalog(self, *, refresh: bool | None) -> PickCatalog:
        """Inventory items then cached-only repos; a footer note says how fresh that is."""
        items: list[PickItem] = []
        known: set[tuple[str, ...]] = set()
        note = ""
        try:
            inventory = self._inventory(refresh)
        except UntapedError as exc:
            if refresh is True:
                raise
            note = f"cached repos only — {exc}"
        else:
            note = _note(inventory)
            for repo in inventory.repos:
                url = repo.clone_url or repo.ssh_url
                known |= {repo_key(u) for u in (repo.clone_url, repo.ssh_url) if u}
                if self._excluded(repo.clone_url, repo.ssh_url):
                    continue
                if url:
                    self._urls[repo.full_name] = url
                items.append(
                    PickItem(
                        id=repo.full_name,
                        label=repo.full_name,
                        description=repo.description or "",
                        dimmed=repo.archived,
                    )
                )
        self._cached = self._scan_cache()
        for ident, key in self._cached.items():
            if key in known or key in self._exclude:
                continue
            origin = self._git.cache_origin(self._cache_dir.joinpath(*key))
            if origin and repo_key(origin) != key:  # rewritten origin: would fill another cache
                continue
            items.append(PickItem(id=ident, label=ident, description="cached"))
        return PickCatalog(items=tuple(items), note=note)

    def url_for(self, item_id: str) -> str | None:
        """Clone URL of ``item_id``: inventory, else cached origin, else the id if it is a URL."""
        if item_id in self._urls:
            return self._urls[item_id]
        if item_id in self._cached:
            return self._cache_url(item_id, self._cached[item_id])
        return item_id if looks_like_url(item_id) else None

    def ident_for(self, item_id: str) -> str:
        """What to resolve for a pick: a cached-only repo's cache URL, else ``item_id``.

        Inventory items keep their ``full_name`` (resolved through the
        inventory); a cached-only repo may come from another host, so its
        ``host/owner/name`` id is never resolved as a GitHub name.
        """
        if item_id in self._cached and item_id not in self._urls:
            return self._cache_url(item_id, self._cached[item_id])
        return item_id

    def branches(self, item_id: str | None) -> list[str]:
        """Cached remote branches of ``item_id`` (no network); ``[]`` for the all-items row."""
        if item_id is None:
            return []
        if item_id in self._branches:
            return self._branches[item_id]
        url = self.url_for(item_id)
        if url is None:
            return []
        self._branches[item_id] = self._git.remote_branches(url)
        return self._branches[item_id]

    # -- helpers -----------------------------------------------------------

    def _excluded(self, *urls: str | None) -> bool:
        return any(repo_key(url) in self._exclude for url in urls if url)

    def _cache_url(self, ident: str, key: tuple[str, ...]) -> str:
        """The cache's ``origin`` URL, else a URL with the same cache identity."""
        origin = self._git.cache_origin(self._cache_dir.joinpath(*key))
        return origin or f"https://{ident}"

    def _scan_cache(self) -> dict[str, tuple[str, ...]]:
        """``host/owner/name`` (any depth) -> :func:`repo_key` path, per ``<host>/.../<name>.git``.

        The id keeps the host, so one ``owner/name`` cached from two hosts is two items.

        A ``*.git`` directory is a leaf: never entered, so its contents are not scanned.
        """
        found: dict[str, tuple[str, ...]] = {}
        stack: list[tuple[str, ...]] = [()]
        while stack:
            parts = stack.pop()
            try:
                entries = sorted(os.scandir(self._cache_dir.joinpath(*parts)), key=lambda e: e.name)
            except OSError:
                continue
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False) or (
                    not parts and entry.name == _UNKNOWN
                ):
                    continue
                key = (*parts, entry.name)
                if not entry.name.endswith(".git"):
                    stack.append(key)
                elif len(key) >= 3:
                    found["/".join((*key[:-1], entry.name.removesuffix(".git")))] = key
        return dict(sorted(found.items()))


def _note(inventory: RepoInventory) -> str:
    if inventory.error:
        return f"stale — {inventory.error}"
    if inventory.refreshed_at is None:
        return "no inventory yet"
    return f"refreshed {_age(datetime.now(UTC) - inventory.refreshed_at)} ago"
