"""The SDK's settings layout: map the raw config file to effective values.

`ProfilesSettingsLayout` layers the picked profiles (``profiles.<name>``)
over the base (``config.yml``'s top level) and is the SDK's only layout:
every plugin resolves through it. Resolving the run's own pick, it also
layers the project's ``.untaped/config.yml`` (:mod:`untaped.runtime.folder_layer`):
renamed keys read as in a profile, a relative path read from the project's
folder, and a setting a project may not set refused (exit 4). A picked profile
that sets a setting only the home's base sets is refused there too.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath
from types import UnionType
from typing import TYPE_CHECKING, Any, Union, get_args, get_origin

from pydantic import BaseModel

from untaped.runtime.profile_resolver import (
    DEFAULT_PROFILE,
    FolderValues,
    Picked,
    base_sections,
    check_defined,
    named_profiles,
    picked_names,
    profile_entries,
    resolve_profiles,
    run_layers,
    write_target,
)
from untaped.sdk._schema import FieldDescriptor, walk_settings
from untaped.sdk.errors import ConfigError
from untaped.sdk.health.deprecated_keys import KeyUse, old_spellings, rename_keys
from untaped.sdk.words import command_line, hint, not_found, run_hint, shown_path

if TYPE_CHECKING:
    from untaped.runtime.folder_layer import FolderLayer

SectionModels = Mapping[str, type[BaseModel]]


@dataclass(frozen=True)
class ResolvedConfig:
    """Effective values, their provenance, and the old keys each layer used."""

    effective: dict[str, Any]
    provenance: dict[tuple[str, ...], str]
    uses: dict[str, dict[str, tuple[KeyUse, ...]]]
    """``profile -> section -> uses``, for the layered profiles (and project) only."""


class ProfilesSettingsLayout:
    """Layer the picked ``profiles.<name>`` over the base, ``config.yml``'s top level, in order.

    Profiles are a core feature, not a plugin, so this layout (and the
    resolver it delegates to) lives in core.

    ``sections`` supplies the default section models: each layered profile's
    section data is rewritten to current key names (``renamed_keys``) before
    layering, so an old key in the base and the new key in the picked
    profile layer as one key. Without it, nothing is renamed.
    """

    def __init__(self, sections: Callable[[], SectionModels] | None = None) -> None:
        self._sections = sections

    def effective(self, raw: dict[str, Any], *, profile: str | None = None) -> dict[str, Any]:
        """Return the effective settings values for the run's pick (or the given profile)."""
        return self.resolve(raw, profile=profile).effective

    def inherited(self, raw: dict[str, Any], profile: str) -> dict[str, Any]:
        """Return the effective values ``profile`` inherits: the base's, nothing for the base."""
        if profile == DEFAULT_PROFILE:
            return {}
        return self.effective(raw, profile=DEFAULT_PROFILE)

    def provenance(self, raw: dict[str, Any]) -> dict[tuple[str, ...], str]:
        """Return ``leaf path -> profile name`` for every value in ``effective``."""
        return self.resolve(raw).provenance

    def resolve(
        self,
        raw: dict[str, Any],
        *,
        profile: str | None = None,
        sections: SectionModels | None = None,
    ) -> ResolvedConfig:
        """Resolve effective settings, provenance and old-key uses.

        ``profile`` layers that one profile over the base instead of the
        run's pick, and leaves the project's file out: a command acting on a
        profile by name reads that profile alone. ``sections`` overrides the
        constructor's section models. A profile the run picks that sets a
        setting of ``sections`` only the home's base sets is refused (exit 4).
        """
        models = sections if sections is not None else self._sections_now()
        if profile:
            picks: tuple[str, ...] = (profile,)
            folder = None
        else:
            layers, layer = run_layers()
            picks = tuple(pick.name for pick in layers)
            refusal = _picked_home_only(raw, models, layers, layer)
            if refusal is not None:
                raise refusal
            folder = None if layer is None else _folder_values(layer, layers, models)
        renamed, uses = _rename_profiles(raw, models, picks)
        if folder is not None:
            values, folder_uses = folder
            if folder_uses:
                uses[values.source] = folder_uses
            effective, provenance = resolve_profiles(renamed, picks=picks, folder=values)
        else:
            effective, provenance = resolve_profiles(renamed, picks=picks)
        return ResolvedConfig(effective, provenance, uses)

    def project_effective(self, raw: dict[str, Any], layer: FolderLayer) -> dict[str, Any]:
        """The values a run in ``layer``'s project reads with no pick of its own.

        The base, the project's picks, then its own values; what a write to
        the project's file is validated against.
        """
        models = self._sections_now()
        picks = tuple(Picked(name, "project") for name in layer.picks)
        values, _ = _folder_values(layer, picks, models)
        renamed, _ = _rename_profiles(raw, models, layer.picks)
        effective, _ = resolve_profiles(renamed, picks=layer.picks, folder=values)
        return effective

    def _sections_now(self) -> SectionModels:
        return self._sections() if self._sections is not None else {}

    def profile_names(self, raw: dict[str, Any]) -> list[str]:
        """Return ``default`` (the base), then the named profiles by name."""
        profiles = raw.get("profiles")
        return [DEFAULT_PROFILE, *(sorted(profiles) if isinstance(profiles, dict) else ())]

    def profile_data(self, raw: dict[str, Any], name: str) -> dict[str, Any] | None:
        """Return one profile's raw values (the base's for ``default``); ``None`` if undefined."""
        if name == DEFAULT_PROFILE:
            return base_sections(raw)
        profiles = raw.get("profiles")
        if not isinstance(profiles, dict):
            return None
        if name in profiles and profiles[name] is None:
            return {}
        data = profiles.get(name)
        return data if isinstance(data, dict) else None

    def write_profile(
        self, raw: dict[str, Any], requested: str | None
    ) -> tuple[dict[str, Any], str]:
        """Return the mapping a write lands in: ``raw`` itself for the base.

        A named target must already exist: this is the guardrail that
        keeps ``untaped --profile typo config set`` from silently creating a
        new profile.
        """
        name = requested or write_target()
        known = named_profiles(raw)
        check_defined(picked_names(), known)  # a run whose pick is undefined writes nothing
        if name == DEFAULT_PROFILE:
            return raw, name
        if name not in known:
            raise ConfigError(
                f"{not_found('profile', name, known=sorted(known))}\n"
                f"{hint(f'profile create {name}')}"
            )
        target = known[name]
        if target is None:
            # An empty ``name:`` entry (YAML null): start it fresh.
            target = raw["profiles"][name] = {}
        return target, name


def _rename_profiles(
    raw: dict[str, Any], models: SectionModels, picks: Sequence[str]
) -> tuple[dict[str, Any], dict[str, dict[str, tuple[KeyUse, ...]]]]:
    """Copy of ``raw`` with the layered profiles' sections at current key names."""
    profiles = raw.get("profiles")
    if not models or not (profiles is None or isinstance(profiles, dict)):
        return raw, {}  # ``resolve_profiles`` reports a malformed ``profiles``
    layered = {DEFAULT_PROFILE, *picks}
    renamed = {**raw, "profiles": dict(profiles or {})}
    uses: dict[str, dict[str, tuple[KeyUse, ...]]] = {}
    for name, data, path in profile_entries(raw):
        if name not in layered:
            continue
        sections: dict[str, Any] = {}
        for section, model in models.items():
            section_data = data.get(section)
            if not isinstance(section_data, dict):
                continue
            sections[section], section_uses = rename_keys(model, section_data)
            if section_uses:
                uses.setdefault(name, {})[section] = section_uses
        if path:
            renamed["profiles"][name] = {**data, **sections}
        else:
            renamed.update(sections)
    if "profiles" not in raw:
        del renamed["profiles"]
    return renamed, uses


def _folder_values(
    layer: FolderLayer, picks: Sequence[Picked], models: SectionModels
) -> tuple[FolderValues, dict[str, tuple[KeyUse, ...]]]:
    """The project's values as the layout layers them, and the old keys they used.

    Raises :class:`ConfigError` (exit 4) naming the first setting the
    project may not set.
    """
    sections: dict[str, Any] = {}
    uses: dict[str, tuple[KeyUse, ...]] = {}
    for section, data in layer.sections.items():
        model = models.get(section)
        if model is None or not isinstance(data, dict):
            sections[section] = copy.deepcopy(data)
            continue
        renamed, section_uses = rename_keys(model, copy.deepcopy(data))
        if section_uses:
            uses[section] = section_uses
        descriptors = walk_settings(model, include_collections=True)
        _refuse_home_settings(section, descriptors, renamed, layer.file)
        sections[section] = _rooted(descriptors, renamed, layer.root)
    at = sum(1 for pick in picks if pick.source == "project")
    return FolderValues(at=at, source=layer.source, sections=sections), uses


def picked_home_only(raw: dict[str, Any], models: SectionModels) -> ConfigError | None:
    """The refusal (exit 4) a run with this pick meets reading ``models``; ``None`` when none.

    What doctor checks first, so its other rows can read the base alone.
    """
    picks, layer = run_layers()
    return _picked_home_only(raw, models, picks, layer)


def _picked_home_only(
    raw: dict[str, Any],
    models: SectionModels,
    picks: Sequence[Picked],
    layer: FolderLayer | None,
) -> ConfigError | None:
    """The refusal of the first home-only setting a pick sets; ``None`` when none sets one.

    Read as the profile spells it, an old key name included. A profile no
    run picks is never read here (doctor names it).
    """
    profiles = raw.get("profiles")
    if not picks or not models or not isinstance(profiles, dict):
        return None  # ``resolve_profiles`` reports a malformed ``profiles``
    for pick in picks:
        data = profiles.get(pick.name)
        if not isinstance(data, dict):
            continue
        for section, model in models.items():
            section_data = data.get(section)
            if not isinstance(section_data, dict):
                continue
            keys = home_only_set(section, model, section_data)
            if keys:
                key = keys[0]
                return ConfigError(
                    f"{key} can't be set in profile {pick.name} "
                    f"(picked by {_picker(pick, layer)}): {PROFILE_REFUSAL}",
                    hint=f"{run_hint(f'--profile {pick.name} config unset {key}')}, then "
                    f"`{command_line(f'config set {key} VALUE')}` if the base should hold it",
                    details={"key": key, "profile": pick.name},
                )
    return None


#: Why a profile may not set a home-only setting: what the refusals say.
PROFILE_REFUSAL = "only the home's base sets it, so every run on this machine reads the same value"


def home_only_set(section: str, model: type[BaseModel], data: dict[str, Any]) -> list[str]:
    """Each home-only setting ``data`` (one profile's ``section``) sets, as ``section.key``.

    An old spelling counts as the key it names.
    """
    renamed, _ = rename_keys(model, data)
    return [
        f"{section}.{descriptor.key}"
        for descriptor in walk_settings(model, include_collections=True)
        if profile_refusal(descriptor) is not None and _at(renamed, descriptor.path) is not _MISSING
    ]


def leave_out_home_only(sections: dict[str, Any], models: SectionModels) -> tuple[str, ...]:
    """Remove from one profile's ``sections`` every setting only the base sets; each, as a key.

    An old spelling goes with the key it names. What ``profile create
    --copy-from`` leaves behind, so the new profile is one a run can pick.
    """
    from untaped.runtime.config_file import unset_at_path  # noqa: PLC0415 - it imports settings

    left: list[str] = []
    for section, model in models.items():
        data = sections.get(section)
        if not isinstance(data, dict):
            continue
        for descriptor in walk_settings(model, include_collections=True):
            if profile_refusal(descriptor) is None:
                continue
            olds = [tuple(old.split(".")) for old in old_spellings(model, descriptor.key)]
            removed = [path for path in (descriptor.path, *olds) if unset_at_path(data, path)]
            if removed:
                left.append(f"{section}.{descriptor.key}")
        if not data and any(key.startswith(f"{section}.") for key in left):
            del sections[section]
    return tuple(left)


def _picker(pick: Picked, layer: FolderLayer | None) -> str:
    """What picked ``pick``, as a refusal names it."""
    if pick.source == "project" and layer is not None:
        return shown_path(layer.file)
    return {"project": "the project", "env": "UNTAPED_PROFILE", "flag": "--profile"}[pick.source]


def folder_refusal(descriptor: FieldDescriptor) -> str | None:
    """Why a project's file may not set ``descriptor``'s setting, or ``None`` when it may.

    Read from the setting's type and marks, never its name: a secret (or a
    map or entry that holds one), a command untaped runs, or a setting marked
    ``HomeOnly()``.
    """
    if descriptor.holds_secret:
        return "it is a secret, which only the home's config.yml or `untaped auth login` holds"
    if descriptor.argv:
        return "it is a command untaped runs, which only the home's config.yml sets"
    if descriptor.home_only:
        return "every project shares it, so only the home's config.yml sets it"
    return None


def profile_refusal(descriptor: FieldDescriptor) -> str | None:
    """Why a named profile may not set ``descriptor``'s setting, or ``None`` when it may.

    A setting marked ``HomeOnly()``, or one a ``Dir(at=...)`` places: a
    profile picked by the folder a command runs in would split what the home
    shares.
    """
    return PROFILE_REFUSAL if descriptor.home_only else None


def _refuse_home_settings(
    section: str, descriptors: Sequence[FieldDescriptor], data: dict[str, Any], file: Path
) -> None:
    for descriptor in descriptors:
        why = folder_refusal(descriptor)
        if why is not None and _at(data, descriptor.path) is not _MISSING:
            raise ConfigError(
                f"{section}.{descriptor.key} can't be set in {file}: {why}",
                hint=f"delete `{section}.{descriptor.key}` from {file}",
                details={"key": f"{section}.{descriptor.key}", "file": str(file)},
            )


def _rooted(
    descriptors: Sequence[FieldDescriptor], data: dict[str, Any], root: Path
) -> dict[str, Any]:
    """``data`` with each relative path setting read from ``root`` (``~`` stays the home's).

    A path setting: a path, or a union with one (``bool | Path``), given as text.
    """
    from untaped.runtime.config_file import set_at_path  # noqa: PLC0415 - it imports settings

    for descriptor in descriptors:
        if not _takes_path(descriptor.annotation):
            continue
        value = _at(data, descriptor.path)
        if isinstance(value, str) and value and not value.startswith("~"):
            path = Path(value)
            if not path.is_absolute():
                set_at_path(data, descriptor.path, str(root / path))
    return data


def _takes_path(annotation: object) -> bool:
    members = get_args(annotation) if get_origin(annotation) in (Union, UnionType) else ()
    return any(
        isinstance(kind, type) and issubclass(kind, PurePath) for kind in (annotation, *members)
    )


_MISSING = object()


def _at(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    cursor: Any = data
    for part in path:
        if not isinstance(cursor, dict) or part not in cursor:
            return _MISSING
        cursor = cursor[part]
    return cursor
