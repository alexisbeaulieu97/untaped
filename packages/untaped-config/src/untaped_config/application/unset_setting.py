"""Use case: remove a single configuration key (from a profile)."""

from __future__ import annotations

from typing import Protocol


class _UnsetCapableRepo(Protocol):
    def unset_value(self, key: str, *, profile: str | None = None) -> bool: ...


class UnsetSetting:
    """Remove ``key`` from the named profile (default = active). Returns
    ``True`` if removed."""

    def __init__(self, repo: _UnsetCapableRepo) -> None:
        self._repo = repo

    def __call__(self, key: str, *, profile: str | None = None) -> bool:
        return self._repo.unset_value(key, profile=profile)
