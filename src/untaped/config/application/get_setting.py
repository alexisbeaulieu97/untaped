"""Use case: read one effective scalar configuration key."""

from __future__ import annotations

from untaped.config.application.context import ToolConfigContext
from untaped.config.application.list_settings import setting_entry_for_descriptor
from untaped.config.application.ports import SettingsReader
from untaped.config.domain import SettingEntry


class GetSetting:
    """Return the effective display entry for a single scalar setting."""

    def __init__(self, repo: SettingsReader, *, context: ToolConfigContext | None = None) -> None:
        self._repo = repo
        self._context = context

    def __call__(self, key: str, *, reveal_secrets: bool = False) -> SettingEntry:
        resolved_key = self._context.resolve_key(key) if self._context is not None else key
        descriptor = self._repo.descriptor(resolved_key)
        return setting_entry_for_descriptor(
            self._repo,
            descriptor,
            reveal_secrets=reveal_secrets,
            include_profile=True,
        )
