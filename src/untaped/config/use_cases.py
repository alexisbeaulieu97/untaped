"""Use cases for the root config command group."""

from __future__ import annotations

from collections.abc import Container
from dataclasses import dataclass
from typing import Any

from untaped.config.models import SettingEntry, Source, display_default, display_value
from untaped.config.ports import SettingsReader, SettingsRepository
from untaped.config_schema import FieldDescriptor
from untaped.errors import ConfigError
from untaped.profile_resolver import DEFAULT_PROFILE

_UNRESOLVED: Any = object()


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
    """Build the root config list — one entry per leaf scalar.

    A section that fails validation does not abort the listing: its leaves
    show their raw (unvalidated) values and the section's error is recorded
    in :attr:`errors` so the caller can warn about it.
    """

    def __init__(self, repo: SettingsReader) -> None:
        self._repo = repo
        self.errors: dict[str, str] = {}

    def __call__(self, *, reveal_secrets: bool = False) -> list[SettingEntry]:
        provenance = self._repo.provenance()
        entries: list[SettingEntry] = []
        for descriptor in self._repo.descriptors():
            section = descriptor.path[0]
            current: Any
            if section in self.errors:
                current = self._repo.raw_setting_value(descriptor)
            else:
                try:
                    current = self._repo.setting_value(descriptor)
                except ConfigError as exc:
                    self.errors[section] = str(exc)
                    current = self._repo.raw_setting_value(descriptor)
            entries.append(
                setting_entry_for_descriptor(
                    self._repo,
                    descriptor,
                    current=current,
                    provenance=provenance,
                    reveal_secrets=reveal_secrets,
                )
            )
        return entries


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
            for path, value in _iter_leaves(profile, (), stop_at=descriptors_by_path):
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
    current: Any = _UNRESOLVED,
    provenance: dict[tuple[str, ...], str] | None = None,
    reveal_secrets: bool = False,
    include_profile: bool = False,
) -> SettingEntry:
    """Build a display-ready entry for one effective scalar setting.

    ``http``/``ui`` are ordinary per-profile settings, so every key resolves
    through the same provenance path (env → profile → default → unset).
    ``current``/``provenance`` may be passed in so a batch caller
    (``ListSettings``) resolves each once instead of per entry.
    """
    if current is _UNRESOLVED:
        current = repo.setting_value(descriptor)
    resolved_provenance = repo.provenance() if provenance is None else provenance
    in_env = repo.env_value_for(descriptor) is not None
    source = _resolve_source(
        in_env,
        _provenance_for(resolved_provenance, descriptor),
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


def _iter_leaves(
    data: dict[str, Any],
    prefix: tuple[str, ...],
    *,
    stop_at: Container[tuple[str, ...]] = (),
) -> list[tuple[tuple[str, ...], Any]]:
    """Yield ``(path, value)`` pairs for every leaf of ``data``.

    A mapping whose path is in ``stop_at`` (a mapping-typed setting such as
    ``ui.symbols``) is one leaf rather than a subtree.
    """
    out: list[tuple[tuple[str, ...], Any]] = []
    for key, value in data.items():
        path = (*prefix, key)
        if isinstance(value, dict) and path not in stop_at:
            out.extend(_iter_leaves(value, path, stop_at=stop_at))
        else:
            out.append((path, value))
    return out


def _provenance_for(
    provenance: dict[tuple[str, ...], str], descriptor: FieldDescriptor
) -> str | None:
    """The profile supplying ``descriptor``; a mapping's comes from its entries.

    Provenance is recorded per scalar leaf, so a mapping setting takes the
    profile of its entries, preferring the selected profile over ``default``
    when both contribute.
    """
    exact = provenance.get(descriptor.path)
    if exact is not None or not descriptor.is_collection:
        return exact
    depth = len(descriptor.path)
    owners = {name for path, name in provenance.items() if path[:depth] == descriptor.path}
    return next(iter(sorted(owners - {DEFAULT_PROFILE})), None) or (
        DEFAULT_PROFILE if owners else None
    )


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
