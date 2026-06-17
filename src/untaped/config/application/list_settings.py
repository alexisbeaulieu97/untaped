"""Use case: list all configurable settings with their effective values & sources."""

from __future__ import annotations

from typing import Any

from untaped.config.application.ports import SettingsReader
from untaped.config.domain import SettingEntry, Source, display_default, display_value
from untaped.config_schema import FieldDescriptor


class ListSettings:
    """Build the ``<tool> config list`` table — one entry per leaf scalar."""

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
    """Build the ``<tool> config list --all-profiles`` table.

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
    include_profile: bool = False,
) -> SettingEntry:
    """Build a display-ready entry for one effective scalar setting.

    Provenance for SDK-owned *global* sections (``ui``, ``http``) is detected
    here for every such key — a top-level block is attributed to ``global`` —
    so the ``get`` and ``list`` surfaces stay consistent without per-section
    special-casing.
    """
    resolved_settings = repo.current_settings() if settings is None else settings
    resolved_provenance = repo.provenance() if provenance is None else provenance
    current = _walk_attr(resolved_settings, descriptor.path)
    in_env = repo.env_value_for(descriptor) is not None
    global_configured = repo.global_section_of(descriptor.key) is not None and _has_path(
        repo.yaml_dict(), descriptor.path
    )
    source = _resolve_source(
        in_env,
        resolved_provenance.get(descriptor.path),
        descriptor,
        current,
        global_configured=global_configured,
    )
    return SettingEntry(
        key=descriptor.key,
        value=display_value(descriptor, current, reveal_secrets=reveal_secrets),
        default=display_default(descriptor),
        source=source,
        profile=source.profile if include_profile else None,
    )


def _has_path(data: dict[str, Any], path: tuple[str, ...]) -> bool:
    """True when ``path`` resolves to a leaf present in the raw YAML dict."""
    cursor: Any = data
    for segment in path:
        if not isinstance(cursor, dict) or segment not in cursor:
            return False
        cursor = cursor[segment]
    return True


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
) -> Source:
    if in_env:
        return Source(kind="env")
    if global_configured:
        return Source(kind="global")
    if scope_name is not None:
        return Source(kind="profile", profile=scope_name)
    if current is None and not (descriptor.has_default and descriptor.default is not None):
        return Source(kind="unset")
    return Source(kind="default")
