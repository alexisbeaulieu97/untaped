"""Use case: set a single configuration key (within a profile)."""

from __future__ import annotations

from untaped.config.application.ports import SettingsRepository


class SetSetting:
    """Validate then persist ``key = value`` in the user's config file.

    ``profile`` overrides the active profile and must already exist (except
    for ``"default"``, which is auto-bootstrapped if missing). Returns the
    resolved target profile name so callers can echo where the write landed.
    """

    def __init__(self, repo: SettingsRepository) -> None:
        self._repo = repo

    def __call__(self, key: str, raw_value: str, *, profile: str | None = None) -> str:
        return self._repo.set_value(key, raw_value, profile=profile)
