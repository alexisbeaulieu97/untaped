"""Use cases for the per-tool config command group."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from untaped.config.models import SettingEntry, Source, display_default, display_value
from untaped.config.ports import SettingsReader, SettingsRepository
from untaped.config_schema import FieldDescriptor
from untaped.errors import ConfigError
from untaped.settings import Settings

if TYPE_CHECKING:
    from untaped.tool import ToolSpec


@dataclass(frozen=True)
class ToolConfigContext:
    """Tool identity needed to resolve user-facing config keys."""

    command: str
    section: str
    profile_fields: frozenset[str]
    state_fields: frozenset[str]

    @classmethod
    def from_spec(cls, spec: ToolSpec) -> ToolConfigContext:
        state_fields: Iterable[str] = (
            spec.state_model.model_fields if spec.state_model is not None else ()
        )
        return cls(
            command=spec.command,
            section=spec.section,
            profile_fields=frozenset(spec.profile_model.model_fields),
            state_fields=frozenset(state_fields),
        )

    def resolve_key(self, key: str) -> str:
        """Map a user key to the concrete config key.

        SDK roots win first: a tool field literally named ``log_level``,
        ``http``, or ``ui`` must not capture those SDK-owned settings.
        """
        first, rest = _split_first(key)
        if first in Settings.model_fields:
            return key
        if first in self.state_fields:
            raise self._state_error(key)
        if first == self.section and rest is not None:
            state_first, _ = _split_first(rest)
            if state_first in self.state_fields:
                raise self._state_error(key)
        if first in self.profile_fields:
            return f"{self.section}.{key}"
        return key

    def _state_error(self, key: str) -> ConfigError:
        return ConfigError(
            f"{key!r} is managed by {self.command} and is not a configurable setting"
        )


def _split_first(key: str) -> tuple[str, str | None]:
    first, sep, rest = key.partition(".")
    return first, rest if sep else None


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

    def __init__(
        self, repo: SettingsRepository, *, context: ToolConfigContext | None = None
    ) -> None:
        self._repo = repo
        self._context = context

    def __call__(self, key: str, raw_value: str, *, profile: str | None = None) -> SetSettingResult:
        resolved_key = self._context.resolve_key(key) if self._context is not None else key
        resolved_profile = self._repo.set_value(resolved_key, raw_value, profile=profile)
        return SetSettingResult(key=resolved_key, profile=resolved_profile)


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
