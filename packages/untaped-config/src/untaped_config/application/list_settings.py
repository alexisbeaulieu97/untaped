"""Use case: list all configurable settings with their effective values & sources."""

from __future__ import annotations

from typing import Any, Protocol

from untaped_core import Settings
from untaped_core.config_schema import FieldDescriptor

from untaped_config.domain import SettingEntry, Source, display_default, display_value


class SettingsRepository(Protocol):
    def descriptors(self) -> list[FieldDescriptor]: ...
    def current_settings(self) -> Settings: ...
    def yaml_dict(self) -> dict[str, Any]: ...
    def env_value_for(self, descriptor: FieldDescriptor) -> str | None: ...
    def provenance(self) -> dict[tuple[str, ...], str]: ...
    def profile_data(self, name: str) -> dict[str, Any] | None: ...
    def profile_names(self) -> list[str]: ...


class ListSettings:
    """Build the ``untaped config list`` table — one entry per leaf scalar."""

    def __init__(self, repo: SettingsRepository) -> None:
        self._repo = repo

    def __call__(self, *, reveal_secrets: bool = False) -> list[SettingEntry]:
        settings = self._repo.current_settings()
        provenance = self._repo.provenance()
        entries: list[SettingEntry] = []
        for descriptor in self._repo.descriptors():
            current = _walk_attr(settings, descriptor.path)
            in_env = self._repo.env_value_for(descriptor) is not None
            source = _resolve_source(in_env, provenance.get(descriptor.path), descriptor, current)
            entries.append(
                SettingEntry(
                    key=descriptor.key,
                    value=display_value(descriptor, current, reveal_secrets=reveal_secrets),
                    default=display_default(descriptor),
                    source=source,
                )
            )
        return entries


class ListAllProfilesSettings:
    """Build the ``untaped config list --all-profiles`` table.

    One entry per ``(profile, key)`` pair where the profile actually sets the
    leaf. Schema defaults are *not* included (use plain ``ListSettings`` for
    the effective view).
    """

    def __init__(self, repo: SettingsRepository) -> None:
        self._repo = repo

    def __call__(self, *, reveal_secrets: bool = False) -> list[SettingEntry]:
        descriptors_by_path = {d.path: d for d in self._repo.descriptors()}
        entries: list[SettingEntry] = []
        for profile_name in self._repo.profile_names():
            profile = self._repo.profile_data(profile_name) or {}
            for path, value in _iter_leaves(profile, ()):
                descriptor = descriptors_by_path.get(path)
                if descriptor is None:
                    # Unknown key under this profile (e.g. typo, leftover); skip
                    # to avoid leaking arbitrary YAML into the table.
                    continue
                entries.append(
                    SettingEntry(
                        key=descriptor.key,
                        value=display_value(descriptor, value, reveal_secrets=reveal_secrets),
                        default=display_default(descriptor),
                        source=Source(kind="profile", profile=profile_name),
                        profile=profile_name,
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


def _iter_leaves(
    data: dict[str, Any], prefix: tuple[str, ...]
) -> list[tuple[tuple[str, ...], Any]]:
    """Yield ``(path, value)`` pairs for every scalar leaf of ``data``."""
    out: list[tuple[tuple[str, ...], Any]] = []
    for key, value in data.items():
        path = (*prefix, key)
        if isinstance(value, dict):
            out.extend(_iter_leaves(value, path))
        else:
            out.append((path, value))
    return out


def _resolve_source(
    in_env: bool,
    profile_name: str | None,
    descriptor: FieldDescriptor,
    current: Any,
) -> Source:
    if in_env:
        return Source(kind="env")
    if profile_name is not None:
        return Source(kind="profile", profile=profile_name)
    if current is None and not (descriptor.has_default and descriptor.default is not None):
        return Source(kind="unset")
    return Source(kind="default")
