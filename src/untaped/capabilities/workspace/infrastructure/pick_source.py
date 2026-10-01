"""The repo picker's catalog: the GitHub inventory plus repos already in the cache.

Inventory items come from ``github.api.repo_inventory`` (imported lazily, so
workspace startup stays free of github modules). Repos only in the bare-cache
directory are added so the picker also works offline or without GitHub
configured. Branch completion reads the local cache only, never the network.
"""

from __future__ import annotations

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
        self._cached: dict[str, Path] = {}
        self._branches: dict[str, list[str]] = {}

    def catalog(self, *, refresh: bool | None) -> PickCatalog:
        """Inventory items then cached-only repos; a footer note says how fresh that is."""
        items: list[PickItem] = []
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
        known = {item.id for item in items}
        self._cached = self._scan_cache()
        for ident, path in self._cached.items():
            if ident in known or (self._exclude and self._excluded(self._cache_url(ident, path))):
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

    def branches(self, item_id: str | None) -> list[str]:
        """Cached remote branches of ``item_id`` (no network); ``[]`` for the all-items row."""
        if item_id is None:
            return []
        if item_id not in self._branches:
            url = self.url_for(item_id)
            self._branches[item_id] = self._git.remote_branches(url) if url else []
        return self._branches[item_id]

    # -- helpers -----------------------------------------------------------

    def _excluded(self, *urls: str | None) -> bool:
        return any(repo_key(url) in self._exclude for url in urls if url)

    def _cache_url(self, ident: str, path: Path) -> str:
        """The cache's ``origin`` URL, else a URL with the same cache identity."""
        origin = self._git.cache_origin(path)
        if origin:
            return origin
        host = path.relative_to(self._cache_dir).parts[0]
        return f"https://{host}/{ident}"

    def _scan_cache(self) -> dict[str, Path]:
        """``owner/name`` (any depth) -> cache path, for ``<host>/<owner>/<name>.git`` caches."""
        found: dict[str, Path] = {}
        if not self._cache_dir.is_dir():
            return found
        for path in sorted(self._cache_dir.rglob("*.git")):
            parts = path.relative_to(self._cache_dir).parts
            if len(parts) < 3 or parts[0] == _UNKNOWN or not path.is_dir():
                continue
            found["/".join((*parts[1:-1], parts[-1].removesuffix(".git")))] = path
        return found


def _note(inventory: RepoInventory) -> str:
    if inventory.error:
        return f"stale — {inventory.error}"
    if inventory.refreshed_at is None:
        return "no inventory yet"
    return f"refreshed {_age(datetime.now(UTC) - inventory.refreshed_at)} ago"
