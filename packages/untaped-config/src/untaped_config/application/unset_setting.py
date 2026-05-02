"""Use case: remove a single configuration key (from a profile)."""

from __future__ import annotations

from typing import Protocol


class _UnsetCapableRepo(Protocol):
    def unset_value(self, key: str, *, profile: str | None = None) -> tuple[bool, str]: ...


class UnsetSetting:
    """Remove ``key`` from the named profile (default = active).

    Returns ``(removed, target_profile)`` where ``removed`` is ``True`` if a
    value was actually removed. An explicit ``--profile`` that names a
    non-existent profile raises ``ConfigError`` — same contract as ``set`` —
    so a typo doesn't masquerade as a clean no-op.
    """

    def __init__(self, repo: _UnsetCapableRepo) -> None:
        self._repo = repo

    def __call__(self, key: str, *, profile: str | None = None) -> tuple[bool, str]:
        return self._repo.unset_value(key, profile=profile)
