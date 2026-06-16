"""Use case: set a single configuration key (within a profile)."""

from __future__ import annotations

from untaped.config.application.ports import SettingsRepository


class SetSetting:
    """Validate then persist ``key = value`` in the user's config file.

    ``profile`` selects the target scope when the active settings layout
    supports scopes (the built-in profiles layout). Returns the resolved
    target scope name so callers can echo where the write landed.
    """

    def __init__(self, repo: SettingsRepository) -> None:
        self._repo = repo

    def __call__(self, key: str, raw_value: str, *, profile: str | None = None) -> str | None:
        return self._repo.set_value(key, raw_value, profile=profile)
