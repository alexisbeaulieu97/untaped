"""Use case: read one effective scalar configuration key."""

from __future__ import annotations

from typing import Any

from untaped.config.application.list_settings import setting_entry_for_descriptor
from untaped.config.application.ports import SettingsReader
from untaped.config.domain import SettingEntry

_GLOBAL_UI_PREFIX = "ui."


class GetSetting:
    """Return the effective display entry for a single scalar setting."""

    def __init__(self, repo: SettingsReader) -> None:
        self._repo = repo

    def __call__(self, key: str, *, reveal_secrets: bool = False) -> SettingEntry:
        if key.startswith(_GLOBAL_UI_PREFIX):
            descriptor = self._repo.ui_descriptor(key)
            return setting_entry_for_descriptor(
                self._repo,
                descriptor,
                reveal_secrets=reveal_secrets,
                global_configured=_has_path(self._repo.yaml_dict(), descriptor.path),
                include_profile=True,
            )
        return setting_entry_for_descriptor(
            self._repo,
            self._repo.descriptor(key),
            reveal_secrets=reveal_secrets,
            include_profile=True,
        )


def _has_path(data: dict[str, Any], path: tuple[str, ...]) -> bool:
    cursor: Any = data
    for segment in path:
        if not isinstance(cursor, dict) or segment not in cursor:
            return False
        cursor = cursor[segment]
    return True
