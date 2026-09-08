"""Use cases for the root config command group."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from untaped.config.models import SettingEntry, Source, display_default, display_value
from untaped.config.ports import SettingsReader, SettingsRepository
from untaped.config_schema import FieldDescriptor


class GetSetting:
    """Return the effective display entry for a single scalar setting."""

    def __init__(self, repo: SettingsReader) -> None:
        self._repo = repo

    def __call__(self, key: str, *, reveal_secrets: bool = False) -> SettingEntry:
        descriptor = self._repo.descriptor(key)
        return setting_entry_for_descriptor(
            self._repo,
            descriptor,
            reveal_secrets=reveal_secrets,
            include_profile=True,
        )


class ListSettings:
    """Build the root config list — one entry per leaf scalar."""

    def __init__(self, repo: SettingsReader) -> None:
        self._repo = repo

    def __call__(self, *, reveal_secrets: bool = False) -> list[SettingEntry]:
        settings = self._repo.current_settings()
        provenance = self._repo.provenance()
        return [
            setting_entry_for_descriptor(
                self._repo,
                descriptor,
                settings=settings,
                provenance=provenance,
                reveal_secrets=reveal_secrets,
            )
            for descriptor in self._repo.descriptors()
        ]


class ListAllProfilesSettings:
    """Build the root config list ``--all-profiles`` table.

    One entry per ``(profile, key)`` pair where the profile actually sets the
    leaf. Schema defaults are *not* included (use plain ``ListSettings`` for
    the effective view).
    """

    def __init__(self, repo: SettingsReader) -> None:
        self._repo = repo

    def __call__(self, *, reveal_secrets: bool = False) -> list[SettingEntry]:
        descriptors_by_path = {d.path: d for d in self._repo.descriptors()}
        entries: list[SettingEntry] = []
        for profile_name in self._repo.profile_names():
            profile = self._repo.profile_data(profile_name) or {}
            for path, value in _iter_leaves(profile, ()):
                descriptor = descriptors_by_path.get(path)
                if descriptor is None:
                    continue
                entries.append(
                    SettingEntry(
                        key=descriptor.key,
                        value=display_value(descriptor, value, reveal_secrets=reveal_secrets),
                        default=display_default(descriptor, reveal_secrets=reveal_secrets),
                        source=Source(kind="profile", profile=profile_name),
                        profile=profile_name,
                    )
                )
        return entries


def setting_entry_for_descriptor(
    repo: SettingsReader,
    descriptor: FieldDescriptor,
    *,
    settings: Any | None = None,
    provenance: dict[tuple[str, ...], str] | None = None,
    reveal_secrets: bool = False,
    include_profile: bool = False,
) -> SettingEntry:
    """Build a display-ready entry for one effective scalar setting.

    ``http``/``ui`` are ordinary per-profile settings, so every key resolves
    through the same provenance path (env → profile → default → unset).
    ``settings``/``provenance`` may be passed in so a batch caller
    (``ListSettings``) resolves each once instead of per entry.
    """
    resolved_settings = repo.current_settings() if settings is None else settings
    resolved_provenance = repo.provenance() if provenance is None else provenance
    current = _walk_attr(resolved_settings, descriptor.path)
    in_env = repo.env_value_for(descriptor) is not None
    source = _resolve_source(
        in_env,
        resolved_provenance.get(descriptor.path),
        descriptor,
        current,
    )
    return SettingEntry(
        key=descriptor.key,
        value=display_value(descriptor, current, reveal_secrets=reveal_secrets),
        default=display_default(descriptor, reveal_secrets=reveal_secrets),
        source=source,
        profile=source.profile if include_profile else None,
    )


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

    def __init__(self, repo: SettingsRepository) -> None:
        self._repo = repo

    def __call__(self, key: str, raw_value: str, *, profile: str | None = None) -> SetSettingResult:
        resolved_profile = self._repo.set_value(key, raw_value, profile=profile)
        return SetSettingResult(key=key, profile=resolved_profile)


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

    def __init__(self, repo: SettingsRepository) -> None:
        self._repo = repo

    def __call__(self, key: str, *, profile: str | None = None) -> UnsetSettingResult:
        removed, resolved_profile = self._repo.unset_value(key, profile=profile)
        return UnsetSettingResult(key=key, removed=removed, profile=resolved_profile)
