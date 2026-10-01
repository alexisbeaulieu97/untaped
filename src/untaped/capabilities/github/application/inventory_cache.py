"""Use case: serve the repository inventory from a metadata cache, refreshing when stale.

The cache is keyed by scope (GitHub host plus orgs and teams), so changing the
scope or profile refetches instead of serving another org's repositories.
Refreshes are serialized by the store's lock; a waiter re-reads the cache and
reuses a refresh another process just finished.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from untaped.capabilities.github.application.ports import InventoryStore
from untaped.capabilities.github.domain.inventory import RepoInventory, RepositoryInventoryItem
from untaped.capability_api import UntapedError

__all__ = ["CachedRepoInventory"]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CachedRepoInventory:
    """Return the inventory for one scope, fetching only when needed."""

    def __init__(
        self,
        store: InventoryStore,
        fetch: Callable[[], tuple[RepositoryInventoryItem, ...]],
        *,
        scope_key: str,
        max_age: timedelta,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._store = store
        self._fetch = fetch
        self._scope_key = scope_key
        self._max_age = max_age
        self._now = now

    def __call__(self, *, refresh: bool | None = None) -> RepoInventory:
        cached = self._usable(self._store.load())
        if refresh is False:
            return cached or RepoInventory(repos=(), refreshed_at=None, scope_key=self._scope_key)
        if refresh is None and cached is not None and self._fresh(cached):
            return cached
        with self._store.lock():
            if refresh is None:
                cached = self._usable(self._store.load())
                if cached is not None and self._fresh(cached):
                    return cached
            try:
                repos = self._fetch()
            except UntapedError as exc:
                if refresh is None and cached is not None:
                    return replace(cached, error=str(exc) or type(exc).__name__)
                raise
            inventory = RepoInventory(
                repos=repos, refreshed_at=self._now(), scope_key=self._scope_key
            )
            self._store.save(inventory)
            return inventory

    def _usable(self, inventory: RepoInventory | None) -> RepoInventory | None:
        if inventory is None or inventory.scope_key != self._scope_key:
            return None
        return inventory

    def _fresh(self, inventory: RepoInventory) -> bool:
        if inventory.refreshed_at is None:
            return False
        return self._now() - inventory.refreshed_at < self._max_age
