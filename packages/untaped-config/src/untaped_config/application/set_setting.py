"""Use case: set a single configuration key."""

from __future__ import annotations

from typing import Protocol


class _SetCapableRepo(Protocol):
    def set_value(self, key: str, raw_value: str) -> None: ...


class SetSetting:
    """Validate then persist ``key = value`` in the user's config file."""

    def __init__(self, repo: _SetCapableRepo) -> None:
        self._repo = repo

    def __call__(self, key: str, raw_value: str) -> None:
        self._repo.set_value(key, raw_value)
