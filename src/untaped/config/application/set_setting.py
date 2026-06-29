"""Use case: set a single configuration key (within a profile)."""

from __future__ import annotations

from dataclasses import dataclass

from untaped.config.application.context import ToolConfigContext
from untaped.config.application.ports import SettingsRepository


@dataclass(frozen=True)
class SetSettingResult:
    """Resolved result of a config write."""

    key: str
    profile: str


class SetSetting:
    """Validate then persist ``key = value`` in the user's config file.

    ``profile`` selects the target profile (defaults to the active one).
    Returns the resolved config key and profile so callers can echo where
    the write landed.
    """

    def __init__(
        self, repo: SettingsRepository, *, context: ToolConfigContext | None = None
    ) -> None:
        self._repo = repo
        self._context = context

    def __call__(self, key: str, raw_value: str, *, profile: str | None = None) -> SetSettingResult:
        resolved_key = self._context.resolve_key(key) if self._context is not None else key
        resolved_profile = self._repo.set_value(resolved_key, raw_value, profile=profile)
        return SetSettingResult(key=resolved_key, profile=resolved_profile)


__all__ = ["SetSetting", "SetSettingResult"]
