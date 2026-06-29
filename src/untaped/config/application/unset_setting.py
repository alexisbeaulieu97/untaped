"""Use case: remove a single configuration key (from a profile)."""

from __future__ import annotations

from dataclasses import dataclass

from untaped.config.application.context import ToolConfigContext
from untaped.config.application.ports import SettingsRepository


@dataclass(frozen=True)
class UnsetSettingResult:
    """Resolved result of a config removal."""

    key: str
    removed: bool
    profile: str


class UnsetSetting:
    """Remove ``key`` from the named profile (default = active).

    Returns the resolved key, whether anything was removed, and the resolved
    profile name. An explicit ``--target-profile`` the layout cannot satisfy
    raises ``ConfigError`` — same contract as ``set``.
    """

    def __init__(
        self, repo: SettingsRepository, *, context: ToolConfigContext | None = None
    ) -> None:
        self._repo = repo
        self._context = context

    def __call__(self, key: str, *, profile: str | None = None) -> UnsetSettingResult:
        resolved_key = self._context.resolve_key(key) if self._context is not None else key
        removed, resolved_profile = self._repo.unset_value(resolved_key, profile=profile)
        return UnsetSettingResult(key=resolved_key, removed=removed, profile=resolved_profile)


__all__ = ["UnsetSetting", "UnsetSettingResult"]
