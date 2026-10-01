"""Cached repository inventory: staleness, scope changes, locking and failures."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from untaped.capabilities.github.application.inventory import RepositoryInventoryItem
from untaped.capabilities.github.application.inventory_cache import CachedRepoInventory
from untaped.capabilities.github.domain.inventory import RepoInventory
from untaped.capability_api import UntapedError

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
API = RepositoryInventoryItem(full_name="acme/api", clone_url="https://github.com/acme/api.git")
WEB = RepositoryInventoryItem(full_name="acme/web", clone_url="https://github.com/acme/web.git")


class MemoryStore:
    def __init__(self, inventory: RepoInventory | None = None) -> None:
        self.inventory = inventory
        self.saves = 0
        self.locks = 0
        self.on_lock: RepoInventory | None = None  # simulate another process refreshing

    def load(self) -> RepoInventory | None:
        return self.inventory

    def save(self, inventory: RepoInventory) -> None:
        self.saves += 1
        self.inventory = inventory

    @contextmanager
    def lock(self) -> Iterator[None]:
        self.locks += 1
        if self.on_lock is not None:
            self.inventory = self.on_lock
        yield


class Fetch:
    def __init__(self, *results: tuple[RepositoryInventoryItem, ...] | Exception) -> None:
        self.results = list(results)
        self.calls = 0

    def __call__(self) -> tuple[RepositoryInventoryItem, ...]:
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _cached(
    age: timedelta,
    *,
    scope: str = "scope-a",
    repos: tuple[RepositoryInventoryItem, ...] = (API,),
) -> RepoInventory:
    return RepoInventory(repos=repos, refreshed_at=NOW - age, scope_key=scope)


def _use(store: MemoryStore, fetch: Fetch, *, scope: str = "scope-a") -> CachedRepoInventory:
    return CachedRepoInventory(
        store, fetch, scope_key=scope, max_age=timedelta(hours=24), now=lambda: NOW
    )


def test_fresh_cache_is_served_without_fetching() -> None:
    store, fetch = MemoryStore(_cached(timedelta(hours=1))), Fetch()
    assert _use(store, fetch)().repos == (API,)
    assert (fetch.calls, store.locks) == (0, 0)


def test_missing_cache_is_fetched_and_saved() -> None:
    store, fetch = MemoryStore(), Fetch((API, WEB))
    inventory = _use(store, fetch)()
    assert inventory.repos == (API, WEB)
    assert inventory.refreshed_at == NOW
    assert store.saves == 1


def test_stale_cache_is_refetched() -> None:
    store, fetch = MemoryStore(_cached(timedelta(hours=25))), Fetch((WEB,))
    assert _use(store, fetch)().repos == (WEB,)


def test_a_different_scope_is_refetched() -> None:
    store, fetch = MemoryStore(_cached(timedelta(hours=1), scope="scope-b")), Fetch((WEB,))
    assert _use(store, fetch)().repos == (WEB,)


def test_a_refresh_by_another_process_while_waiting_for_the_lock_is_reused() -> None:
    store, fetch = MemoryStore(_cached(timedelta(hours=25))), Fetch()
    store.on_lock = _cached(timedelta(seconds=1), repos=(WEB,))
    assert _use(store, fetch)().repos == (WEB,)
    assert fetch.calls == 0


def test_forced_refresh_always_fetches() -> None:
    store, fetch = MemoryStore(_cached(timedelta(seconds=1))), Fetch((WEB,))
    assert _use(store, fetch)(refresh=True).repos == (WEB,)
    assert (store.saves, store.locks) == (1, 1)


def test_refresh_false_never_fetches() -> None:
    assert _use(MemoryStore(), Fetch())(refresh=False).repos == ()
    stale = MemoryStore(_cached(timedelta(days=9)))
    assert _use(stale, Fetch())(refresh=False).repos == (API,)


def test_refresh_false_ignores_another_scope() -> None:
    other = MemoryStore(_cached(timedelta(hours=1), scope="scope-b"))
    assert _use(other, Fetch())(refresh=False).repos == ()


def test_auto_refresh_failure_serves_the_stale_cache_with_the_error() -> None:
    store = MemoryStore(_cached(timedelta(hours=25)))
    inventory = _use(store, Fetch(UntapedError("HTTP 503")))()
    assert inventory.repos == (API,)
    assert inventory.error == "HTTP 503"
    assert store.saves == 0


def test_auto_refresh_failure_without_a_cache_raises() -> None:
    with pytest.raises(UntapedError, match="HTTP 503"):
        _use(MemoryStore(), Fetch(UntapedError("HTTP 503")))()


def test_forced_refresh_failure_raises_even_with_a_cache() -> None:
    store = MemoryStore(_cached(timedelta(hours=1)))
    with pytest.raises(UntapedError):
        _use(store, Fetch(UntapedError("HTTP 503")))(refresh=True)
