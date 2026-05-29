"""Use case: remove a single configuration key (from a profile)."""

from __future__ import annotations

from untaped.config.application.ports import SettingsRepository


class UnsetSetting:
    """Remove ``key`` from the named profile (default = active).

    Returns ``(removed, target_profile)`` where ``removed`` is ``True`` if a
    value was actually removed. An explicit ``--target-profile`` that names a
    non-existent profile raises ``ConfigError`` — same contract as ``set``.
    """

    def __init__(self, repo: SettingsRepository) -> None:
        self._repo = repo

    def __call__(self, key: str, *, profile: str | None = None) -> tuple[bool, str]:
        return self._repo.unset_value(key, profile=profile)
