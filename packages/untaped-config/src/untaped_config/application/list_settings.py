"""Use case: list all configurable settings with their effective values & sources."""

from __future__ import annotations

from typing import Any, Protocol

from untaped_core import Settings
from untaped_core.config_file import MISSING, get_at_path
from untaped_core.config_schema import FieldDescriptor

from untaped_config.domain import SettingEntry, Source
from untaped_config.infrastructure.settings_repo import display_default, display_value


class SettingsRepository(Protocol):
    def descriptors(self) -> list[FieldDescriptor]: ...
    def current_settings(self) -> Settings: ...
    def yaml_dict(self) -> dict[str, Any]: ...
    def env_value_for(self, descriptor: FieldDescriptor) -> str | None: ...


class ListSettings:
    """Build the ``untaped config list`` table — one entry per leaf scalar."""

    def __init__(self, repo: SettingsRepository) -> None:
        self._repo = repo

    def __call__(self, *, reveal_secrets: bool = False) -> list[SettingEntry]:
        settings = self._repo.current_settings()
        yaml_dict = self._repo.yaml_dict()
        entries: list[SettingEntry] = []
        for descriptor in self._repo.descriptors():
            current = _walk_attr(settings, descriptor.path)
            in_env = self._repo.env_value_for(descriptor) is not None
            in_yaml = get_at_path(yaml_dict, descriptor.path) is not MISSING
            source = _resolve_source(in_env, in_yaml, descriptor, current)
            entries.append(
                SettingEntry(
                    key=descriptor.key,
                    value=display_value(descriptor, current, reveal_secrets=reveal_secrets),
                    default=display_default(descriptor),
                    source=source,
                    is_secret=descriptor.is_secret,
                )
            )
        return entries


def _walk_attr(obj: Any, path: tuple[str, ...]) -> Any:
    cur = obj
    for key in path:
        cur = getattr(cur, key, None)
        if cur is None:
            return None
    return cur


def _resolve_source(
    in_env: bool,
    in_yaml: bool,
    descriptor: FieldDescriptor,
    current: Any,
) -> Source:
    if in_env:
        return "env"
    if in_yaml:
        return "yaml"
    if current is None and not (descriptor.has_default and descriptor.default is not None):
        return "unset"
    return "default"
