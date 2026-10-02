"""The repo picker's catalog: the GitHub inventory plus repos already in the cache.

Inventory items come from ``github.api.repo_inventory`` (imported lazily, so
workspace startup stays free of github modules). Repos only in the bare-cache
directory (:meth:`GitWorktrees.cached_repos`) are added so the picker also
works offline or without GitHub configured. Branch completion reads the local
cache only, never the network.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from untaped.capabilities.workspace.domain.models import CachedRepo, RepoArg
from untaped.capabilities.workspace.domain.naming import looks_like_url
from untaped.sdk import PickCatalog, PickItem, UntapedError, cache_key

if TYPE_CHECKING:
    from untaped.capabilities.github.api import RepoInventory
    from untaped.capabilities.workspace.application.ports import GitWorktrees

type _Key = tuple[str, ...]
type _Entry = str | CachedRepo | None
"""A loaded item: an inventory repo's clone URL (``None``: it has none), or a cached-only repo."""


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

    ``exclude`` holds the cache identities (:func:`cache_key`) of repos the
    workspace already has; they are not offered.
    """

    def __init__(
        self,
        *,
        git: GitWorktrees,
        inventory: Callable[[bool | None], RepoInventory] | None = None,
        exclude: Collection[_Key] = (),
    ) -> None:
        self._git = git
        self._inventory = inventory or _default_inventory
        self._exclude = frozenset(exclude)
        self._loaded: dict[str, _Entry] = {}
        """The last catalog's items by id, rebuilt on every load."""
        self._seen: dict[str, str] = {}
        """Inventory id -> URL across loads (never cleared): a pick a refresh dropped keeps it."""
        self._branches: dict[str, list[str]] = {}

    def catalog(self, *, refresh: bool | None) -> PickCatalog:
        """Inventory items then cached-only repos; a footer note says how fresh that is."""
        note, inventory, known = self._inventory_items(refresh)
        cached = self._cached_items(known, taken={item.id for item, _ in inventory})
        loaded: list[tuple[PickItem, _Entry]] = [*inventory, *cached]
        self._loaded = {item.id: entry for item, entry in loaded}
        self._seen.update({item.id: url for item, url in inventory if url})
        return PickCatalog(items=tuple(item for item, _ in loaded), note=note)

    def url_for(self, item_id: str) -> str | None:
        """Clone URL of ``item_id``: inventory, else cached origin, else the id if it is a URL."""
        entry = self._loaded.get(item_id)
        if isinstance(entry, CachedRepo):
            return _cache_url(entry)
        return entry or self._seen.get(item_id) or (item_id if looks_like_url(item_id) else None)

    def pick_arg(self, arg: RepoArg) -> RepoArg:
        """``arg`` (its ident a picked item id) with what to resolve and the URL to fall back on.

        Inventory items keep their ``full_name`` (resolved through the
        inventory), falling back on the URL last seen for it, so a pick that
        a refresh dropped still provisions; a cached-only repo may come from
        another host, so its ``host/owner/name`` id is never resolved as a
        GitHub name: its cache URL is resolved instead.
        """
        entry = self._loaded.get(arg.ident)
        if isinstance(entry, CachedRepo):
            return arg.model_copy(update={"ident": _cache_url(entry), "fallback": None})
        return arg.model_copy(update={"fallback": self._seen.get(arg.ident)})

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

    def _inventory_items(
        self, refresh: bool | None
    ) -> tuple[str, list[tuple[PickItem, str | None]], set[_Key]]:
        """The footer note, the offered inventory repos with their URLs, and every repo's key."""
        try:
            inventory = self._inventory(refresh)
        except UntapedError as exc:
            if refresh is True:
                raise
            return f"cached repos only — {exc}", [], set()
        items: list[tuple[PickItem, str | None]] = []
        known: set[_Key] = set()
        for repo in inventory.repos:
            keys = {cache_key(url) for url in (repo.clone_url, repo.ssh_url) if url}
            known |= keys
            if keys & self._exclude:
                continue
            item = PickItem(
                id=repo.full_name,
                label=repo.full_name,
                description=repo.description or "",
                dimmed=repo.archived,
            )
            items.append((item, repo.clone_url or repo.ssh_url))
        return _note(inventory), items, known

    def _cached_items(
        self, known: Collection[_Key], *, taken: Collection[str]
    ) -> list[tuple[PickItem, CachedRepo]]:
        """Cached repos neither in ``known`` (the inventory's) nor excluded.

        A cached id in ``taken`` (the inventory's ids) is skipped: an owner-less
        cache on a dotless host (``acme/api.git``) would otherwise shadow the
        inventory's ``acme/api``.
        """
        items: list[tuple[PickItem, CachedRepo]] = []
        for cached in self._git.cached_repos():
            if cached.key in known or cached.key in self._exclude or cached.ident in taken:
                continue
            if cached.origin and cache_key(cached.origin) != cached.key:
                continue  # rewritten origin: would fill another cache
            items.append(
                (PickItem(id=cached.ident, label=cached.ident, description="cached"), cached)
            )
        return items


def _cache_url(cached: CachedRepo) -> str:
    """The cache's ``origin`` URL, else a URL with the same cache identity."""
    return cached.origin or f"https://{cached.ident}"


def _note(inventory: RepoInventory) -> str:
    if inventory.error:
        return f"stale — {inventory.error}"
    if inventory.refreshed_at is None:
        return "no inventory yet"
    return f"refreshed {_age(datetime.now(UTC) - inventory.refreshed_at)} ago"
