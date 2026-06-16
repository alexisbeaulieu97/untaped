"""Use case: list all configurable settings with their effective values & sources."""

from __future__ import annotations

from typing import Any

from untaped.config.application.ports import SettingsReader
from untaped.config.domain import SettingEntry, Source, display_default, display_value
from untaped.config_schema import FieldDescriptor


class ListSettings:
    """Build the ``untaped config list`` table — one entry per leaf scalar."""

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
    """Build the ``untaped config list --all-profiles`` table.

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
                        default=display_default(descriptor),
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
    global_configured: bool = False,
    include_profile: bool = False,
) -> SettingEntry:
    """Build a display-ready entry for one effective scalar setting."""
    resolved_settings = repo.current_settings() if settings is None else settings
    resolved_provenance = repo.provenance() if provenance is None else provenance
    current = _walk_attr(resolved_settings, descriptor.path)
    in_env = repo.env_value_for(descriptor) is not None
    source = _resolve_source(
        in_env,
        resolved_provenance.get(descriptor.path),
        descriptor,
        current,
        global_configured=global_configured,
        scoped=repo.supports_profiles(),
    )
    return SettingEntry(
        key=descriptor.key,
        value=display_value(descriptor, current, reveal_secrets=reveal_secrets),
        default=display_default(descriptor),
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
    scope_name: str | None,
    descriptor: FieldDescriptor,
    current: Any,
    *,
    global_configured: bool = False,
    scoped: bool = False,
) -> Source:
    if in_env:
        return Source(kind="env")
    if global_configured:
        return Source(kind="global")
    if scope_name is not None:
        # A provenance hit names the supplying profile only when the active
        # layout actually has scopes; the flat layout's provenance marker
        # must not masquerade as a profile named "config".
        if scoped:
            return Source(kind="profile", profile=scope_name)
        return Source(kind="config")
    if current is None and not (descriptor.has_default and descriptor.default is not None):
        return Source(kind="unset")
    return Source(kind="default")
