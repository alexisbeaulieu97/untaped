"""Use case: set a single configuration key (within a profile)."""

from __future__ import annotations

from typing import Protocol


class _SetCapableRepo(Protocol):
    def set_value(self, key: str, raw_value: str, *, profile: str | None = None) -> None: ...


class SetSetting:
    """Validate then persist ``key = value`` in the user's config file.

    ``profile`` overrides the active profile and must already exist (except
    for ``"default"``, which is auto-bootstrapped if missing).
    """

    def __init__(self, repo: _SetCapableRepo) -> None:
        self._repo = repo

    def __call__(self, key: str, raw_value: str, *, profile: str | None = None) -> None:
        self._repo.set_value(key, raw_value, profile=profile)
