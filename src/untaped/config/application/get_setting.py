"""Use case: read one effective scalar configuration key."""

from __future__ import annotations

from untaped.config.application.list_settings import setting_entry_for_descriptor
from untaped.config.application.ports import SettingsReader
from untaped.config.domain import SettingEntry


class GetSetting:
    """Return the effective display entry for a single scalar setting."""

    def __init__(self, repo: SettingsReader) -> None:
        self._repo = repo

    def __call__(self, key: str, *, reveal_secrets: bool = False) -> SettingEntry:
        section = self._repo.global_section_of(key)
        descriptor = (
            self._repo.global_descriptor(key, section)
            if section is not None
            else self._repo.descriptor(key)
        )
        return setting_entry_for_descriptor(
            self._repo,
            descriptor,
            reveal_secrets=reveal_secrets,
            include_profile=True,
        )
