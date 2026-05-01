"""Use case: remove a single configuration key."""

from __future__ import annotations

from typing import Protocol


class _UnsetCapableRepo(Protocol):
    def unset_value(self, key: str) -> bool: ...


class UnsetSetting:
    """Remove ``key`` from the user's config file. Returns ``True`` if removed."""

    def __init__(self, repo: _UnsetCapableRepo) -> None:
        self._repo = repo

    def __call__(self, key: str) -> bool:
        return self._repo.unset_value(key)
