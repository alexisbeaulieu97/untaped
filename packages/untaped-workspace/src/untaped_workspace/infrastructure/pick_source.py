"""The repo picker's catalog: the GitHub inventory plus repos already in the repo store.

Inventory items come from ``github.api.repo_inventory`` (imported lazily, so
workspace startup stays free of github modules). Repos workspace has used that
only the repo store holds (:meth:`GitWorktrees.stored_repos`) are added so the
picker also works offline or without GitHub configured. Branch completion
reads the store only, never the network.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from untaped.sdk import PickCatalog, PickItem, UntapedError
from untaped_workspace.domain.models import RepoArg, StoredRepo
from untaped_workspace.domain.naming import looks_like_url, repo_key

if TYPE_CHECKING:
    from untaped_github.api import RepoInventory
    from untaped_workspace.application.ports import GitWorktrees

type _Key = tuple[str, ...]
type _Entry = str | StoredRepo | None
"""A loaded item: an inventory repo's clone URL (``None``: it has none), or a stored-only repo."""


def _default_inventory(refresh: bool | None) -> RepoInventory:
    from untaped_github.api import repo_inventory  # noqa: PLC0415

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
    """Picker items from the inventory and the repo store, plus branch completion.

    ``exclude`` holds the repo keys (:func:`repo_key`) of repos the workspace
    already has; they are not offered.
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
        """Inventory items then stored-only repos; a footer note says how fresh that is."""
        note, inventory, known = self._inventory_items(refresh)
        stored = self._stored_items(known, taken={item.id for item, _ in inventory})
        loaded: list[tuple[PickItem, _Entry]] = [*inventory, *stored]
        self._loaded = {item.id: entry for item, entry in loaded}
        self._seen.update({item.id: url for item, url in inventory if url})
        return PickCatalog(items=tuple(item for item, _ in loaded), note=note)

    def url_for(self, item_id: str) -> str | None:
        """Clone URL of ``item_id``: inventory, else the stored URL, else the id if it is a URL."""
        entry = self._loaded.get(item_id)
        if isinstance(entry, StoredRepo):
            return entry.origin
        return entry or self._seen.get(item_id) or (item_id if looks_like_url(item_id) else None)

    def pick_arg(self, arg: RepoArg) -> RepoArg:
        """``arg`` (its ident a picked item id) with what to resolve and the URL to fall back on.

        Inventory items keep their ``full_name`` (resolved through the
        inventory), falling back on the URL last seen for it, so a pick that
        a refresh dropped still provisions; a stored-only repo may come from
        another host, so its ``host/owner/name`` id is never resolved as a
        GitHub name: its stored URL is resolved instead.
        """
        entry = self._loaded.get(arg.ident)
        if isinstance(entry, StoredRepo):
            return arg.model_copy(update={"ident": entry.origin, "fallback": None})
        return arg.model_copy(update={"fallback": self._seen.get(arg.ident)})

    def branches(self, item_id: str | None) -> list[str]:
        """Stored remote branches of ``item_id`` (no network); ``[]`` for the all-items row."""
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
            return f"stored repos only — {exc}", [], set()
        items: list[tuple[PickItem, str | None]] = []
        known: set[_Key] = set()
        for repo in inventory.repos:
            keys = {repo_key(url) for url in (repo.clone_url, repo.ssh_url) if url}
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

    def _stored_items(
        self, known: Collection[_Key], *, taken: Collection[str]
    ) -> list[tuple[PickItem, StoredRepo]]:
        """Stored repos neither in ``known`` (the inventory's) nor excluded.

        A stored id in ``taken`` (the inventory's ids) is skipped: an owner-less
        repo on a dotless host (``acme/api.git``) would otherwise shadow the
        inventory's ``acme/api``.
        """
        items: list[tuple[PickItem, StoredRepo]] = []
        for stored in self._git.stored_repos():
            if stored.key in known or stored.key in self._exclude or stored.ident in taken:
                continue
            items.append(
                (PickItem(id=stored.ident, label=stored.ident, description="repo store"), stored)
            )
        return items


def _note(inventory: RepoInventory) -> str:
    if inventory.error:
        return f"stale — {inventory.error}"
    if inventory.refreshed_at is None:
        return "no inventory yet"
    return f"refreshed {_age(datetime.now(UTC) - inventory.refreshed_at)} ago"
