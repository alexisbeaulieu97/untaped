"""Use cases for the root config command group.

The reads (:class:`GetSetting`, :class:`ListSettings`) and the writes
(:class:`SetSetting`, :class:`SetSettings`, :class:`UnsetSetting`,
:class:`RankProviders`) are
what every front shares: ``config``, ``plugin rank`` and the setup screen
call the same code, so they cannot disagree about what a write does. A
write prints nothing; it returns its outcome record and the lines a front
prints about it (:data:`Note`).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Container, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter, ValidationError

from untaped.core.config.models import (
    OldSpelling,
    SettingEntry,
    SettingOutcome,
    Source,
    display_default,
    display_value,
)
from untaped.core.config.ports import SettingsReader, SettingsWriter
from untaped.runtime.profile_resolver import DEFAULT_PROFILE
from untaped.runtime.settings import ContractName, ExtensionSettings, resolve_config_path
from untaped.sdk._schema import FieldDescriptor
from untaped.sdk.errors import ConfigError, UsageError, first_validation_error
from untaped.sdk.health.deprecated_keys import rename_keys
from untaped.sdk.marks import Deprecated, owner, removal_text, replacement_text, stability_name
from untaped.sdk.words import removal_phrase, run_hint

if TYPE_CHECKING:
    from untaped.runtime.render import MessageKind
    from untaped.sdk.contracts._declare import ContractInfo

_UNRESOLVED: Any = object()

type Note = tuple[MessageKind, str]
"""A line about a write, for the front to print (at once, or when a screen closes)."""


@dataclass(frozen=True)
class SettingChange:
    """What a config write did: its outcome record and the lines to print about it."""

    outcome: SettingOutcome
    notes: tuple[Note, ...] = ()


class SetSetting:
    """Write one setting's value in a profile, validated as its section is."""

    def __init__(self, repo: SettingsWriter) -> None:
        self._repo = repo

    def current(self, key: str, *, profile: str | None = None) -> Any:
        """``key``'s raw value in ``profile``'s own data, which a later ``expected`` names."""
        return self._repo.own_value(key, profile=profile)

    def __call__(
        self,
        key: str,
        value: str,
        *,
        profile: str | None = None,
        dry_run: bool = False,
        expected: Any = _UNRESOLVED,
    ) -> SettingChange:
        """Set ``key`` to the raw ``value`` (as ``config set`` takes it) in ``profile``.

        ``profile`` defaults to the active one. Writing a key removes its old
        spellings in that profile. ``expected`` (from :meth:`current`) refuses
        the write when the profile's own value changed since it was read, as
        ``config edit`` refuses a file changed under it. Raises
        :class:`ConfigError` when the value is invalid or changed; nothing is
        written then.
        """
        removed: list[str] = []

        def update(target: str, current: Any) -> str:
            if expected is not _UNRESOLVED and current != expected:
                raise ConfigError(
                    f"{key} changed in profile {target} while it was open; nothing was written",
                    category="conflict",
                )
            return value

        target = self._repo.update_value(
            key, update, profile=profile, dry_run=dry_run, on_spelling_removed=removed.append
        )
        if dry_run:
            return SettingChange(SettingOutcome(key=key, profile=target, action="planned"))
        line = f"set {key} in profile {target}{_removed_note(removed)}"
        return SettingChange(
            SettingOutcome(key=key, profile=target, action="updated"),
            (("success", f"{line} (config: {resolve_config_path()})"),),
        )


class UnsetSetting:
    """Remove one setting, and its old spellings, from a profile."""

    def __init__(self, repo: SettingsWriter) -> None:
        self._repo = repo

    def __call__(
        self, key: str, *, profile: str | None = None, dry_run: bool = False
    ) -> SettingChange:
        """Unset ``key`` in ``profile`` (the active one by default); a key not set is unchanged."""
        spellings: list[str] = []
        removed, target = self._repo.unset_value(
            key, profile=profile, dry_run=dry_run, on_spelling_removed=spellings.append
        )
        where = f"in profile {target}"
        notes: tuple[Note, ...] = ()
        if not removed:
            notes = (("info", f"{key} was not set {where}"),)
        elif not dry_run:
            notes = (("success", f"unset {key} {where}{_removed_note(spellings)}"),)
        action = "unchanged" if not removed else "planned" if dry_run else "deleted"
        return SettingChange(SettingOutcome(key=key, profile=target, action=action), notes)


class SetSettings:
    """Write several settings in a profile as one change, validated together: all or none."""

    def __init__(self, repo: SettingsWriter) -> None:
        self._repo = repo

    def __call__(
        self,
        values: Mapping[str, str | None],
        *,
        profile: str | None = None,
        expected: Mapping[str, Any] | None = None,
    ) -> tuple[SettingChange, ...]:
        """Set each key to its raw value (``None`` unsets it) in ``profile``, as one write.

        Keys that are only valid together (a server's URL and its TLS setting)
        are validated with each other in place. ``expected`` maps a key to its
        own value when it was read (:meth:`SetSetting.current`): one key
        changed since refuses the whole write. Raises :class:`ConfigError`
        when a value is invalid or changed; nothing is written then. One
        change per key, in ``values``' order.
        """
        expected = expected or {}
        removed: list[str] = []
        had: dict[str, bool] = {}

        def updater(key: str, value: str | None) -> Callable[[str, Any], str | None]:
            def update(target: str, current: Any) -> str | None:
                if key in expected and current != expected[key]:
                    raise ConfigError(
                        f"{key} changed in profile {target} while it was open; nothing was written",
                        category="conflict",
                    )
                had[key] = current is not None
                return value

            return update

        target = self._repo.update_values(
            {key: updater(key, value) for key, value in values.items()},
            profile=profile,
            on_spelling_removed=removed.append,
        )
        changes: list[SettingChange] = []
        for key, value in values.items():
            if value is None and not had.get(key):
                outcome = SettingOutcome(key=key, profile=target, action="unchanged")
                changes.append(SettingChange(outcome))
                continue
            verb, action = ("unset", "deleted") if value is None else ("set", "updated")
            # The old spellings removed go on the first line, as one write removed them.
            line = f"{verb} {key} in profile {target}{_removed_note(removed)}"
            removed = []
            outcome = SettingOutcome(key=key, profile=target, action=action)
            changes.append(SettingChange(outcome, (("success", line),)))
        return tuple(changes)


_EXTENSIONS = TypeAdapter(dict[ContractName, ExtensionSettings])


class RankProviders:
    """Order the providers of a contract method in a profile's ``<owner>.extensions``.

    ``owners`` are the composed plugins that own contracts. The contract and
    method must exist; a provider that doesn't fill the method is kept, with
    a warning, for when it does.
    """

    def __init__(self, repo: SettingsWriter, owners: Collection[str]) -> None:
        self._repo = repo
        self._owners = owners

    def current(self, contract: str, method: str, *, profile: str | None = None) -> Any:
        """The ranking ``profile`` sets itself, which a later ``expected`` names (``None``: none).

        ``contract`` is ``OWNER.CONTRACT``.
        """
        owner_name, _, name = contract.partition(".")
        return _rank_of(
            self._repo.own_value(f"{owner_name}.extensions", profile=profile), name, method
        )

    def __call__(
        self,
        contract: str,
        method: str,
        plugins: list[str],
        *,
        profile: str | None = None,
        dry_run: bool = False,
        expected: Any = _UNRESOLVED,
    ) -> SettingChange:
        """Write ``plugins`` as the ranking of ``contract`` (``OWNER.CONTRACT``) ``method``.

        No plugins removes the ranking. Only the owner must exist then:
        removing is how doctor repairs a ranking of a contract or method that
        doesn't. Raises :class:`UsageError` for a name that doesn't exist.
        ``expected`` (from :meth:`current`) refuses the write with a
        :class:`ConfigError` when the profile's ranking changed since it was read.
        """
        owner_name, _, name = contract.partition(".")
        if not owner_name or not name:
            raise UsageError(f"name the contract as OWNER.CONTRACT, not {contract!r}")
        try:
            _EXTENSIONS.validate_python({name: {"rank": {method: plugins}}})
        except ValidationError as exc:
            raise UsageError(f"invalid ranking: {first_validation_error(exc)}") from None
        if owner_name not in self._owners:
            owners = ", ".join(sorted(self._owners)) or "none"
            raise UsageError(
                f"{owner_name!r} owns no contract (contract owners: {owners})",
                hint=run_hint("plugin contracts"),
            )
        notes: list[Note] = []
        if plugins:
            notes.extend(_check_rankable(owner_name, name, method, plugins))
        changed = False

        def update(target: str, current: Any) -> str | None:
            nonlocal changed
            if expected is not _UNRESOLVED and _rank_of(current, name, method) != expected:
                raise ConfigError(
                    f"{owner_name}.{name} {method} changed in profile {target} while it was "
                    "open; nothing was written",
                    category="conflict",
                )
            extensions = _ranked(_mapping(current), name, method, plugins)
            changed = extensions != current and bool(extensions or current)
            return json.dumps(extensions) if extensions else None

        key = f"{owner_name}.extensions"
        path = f"{key}.{name}.rank.{method}"
        target = self._repo.update_value(key, update, profile=profile, dry_run=dry_run)
        if not changed:
            notes.append(("info", f"{path} unchanged in profile {target}"))
            action = "unchanged"
        elif dry_run:
            action = "planned"
        else:
            done = f"ranked {' > '.join(plugins)}" if plugins else "removed the ranking"
            notes.append(("success", f"{done} for {owner_name}.{name} {method} (profile {target})"))
            action = "updated" if plugins else "deleted"
        return SettingChange(SettingOutcome(key=key, profile=target, action=action), tuple(notes))


def _removed_note(spellings: list[str]) -> str:
    return f"; removed {', '.join(spellings)}" if spellings else ""


def _mapping(value: Any) -> dict[str, Any]:
    """``value`` as a mapping to edit; anything else (``null``, a stray list) starts empty."""
    return dict(value) if isinstance(value, dict) else {}


def _rank_of(extensions: Any, name: str, method: str) -> Any:
    """``name``'s ``method`` ranking in raw ``extensions`` data, ``None`` when it sets none."""
    return _mapping(_mapping(_mapping(extensions).get(name)).get("rank")).get(method)


def _ranked(
    extensions: dict[str, Any], name: str, method: str, plugins: list[str]
) -> dict[str, Any]:
    """``extensions`` with ``name``'s ``method`` ranked as ``plugins`` (none: unranked).

    A malformed contract or ``rank`` value is replaced, so ranking repairs it;
    an emptied contract entry is dropped.
    """
    entry = _mapping(extensions.get(name))
    ranks = _mapping(entry.get("rank"))
    if plugins:
        ranks[method] = plugins
    else:
        ranks.pop(method, None)
    if ranks:
        entry["rank"] = ranks
    else:
        entry.pop("rank", None)
    if entry:
        extensions[name] = entry
    else:
        extensions.pop(name, None)
    return extensions


def _check_rankable(owner_name: str, name: str, method: str, plugins: list[str]) -> list[Note]:
    """Refuse a contract or method that doesn't exist; warn about a plugin that doesn't fill it."""
    from untaped.runtime.contracts.registry import (  # noqa: PLC0415 - only when ranking
        method_providers,
        owned_contracts,
    )

    owned = owned_contracts()
    if owner_name not in owned:
        raise ConfigError(
            f"{owner_name}'s contracts couldn't be read",
            system=owner_name,
            hint=run_hint("doctor"),
        )
    info: ContractInfo | None = next(
        (each for each in owned[owner_name] if each.name == name), None
    )
    if info is None:
        known = ", ".join(sorted(each.name for each in owned[owner_name]))
        raise UsageError(f"{owner_name} declares no contract {name!r} (it declares {known})")
    rankable = [each for each, declared in info.methods.items() if not declared.bridge]
    if method not in rankable:
        what = "is a bridge, which is never ranked" if method in info.methods else "isn't a method"
        raise UsageError(
            f"{owner_name}.{name} {method} {what} (ranked methods: {', '.join(rankable)})"
        )
    filling = set(method_providers(info, method, ()))
    return [
        (
            "warning",
            f"{plugin} doesn't fill {owner_name}.{name} {method} here; the ranking keeps it "
            "for when it does",
        )
        for plugin in plugins
        if plugin not in filling
    ]


@dataclass(frozen=True)
class NamedValue:
    """One name of a mapping that declares its names (``untaped.ui.symbols``), and its source."""

    name: str
    value: str | None
    """``None`` when nothing sets the name, not even its defaults."""
    source: str
    """The profile (``profile:work``) or variable setting it, else its defaults' (``theme``)."""
    declared: bool = True
    """Whether the field's :class:`~untaped.sdk._schema.Choices` names it."""
    variable: str | None = None
    """The variable setting it, which wins over any profile's value."""


class ListNames:
    """Each name a declared-name mapping accepts, with its effective value and source.

    A name the mapping sets shows that value and the profile or variable
    setting it; any other declared name shows the default its
    :class:`~untaped.sdk._schema.Choices` gives. Names the mapping sets
    that aren't declared follow, so a stray one can be seen and removed.
    """

    def __init__(self, repo: SettingsReader) -> None:
        self._repo = repo

    def __call__(self, key: str, *, under: str | None = None) -> tuple[NamedValue, ...]:
        """The names of ``key``; raises :class:`ConfigError` for a key that declares none.

        With ``under`` (a profile), a name that profile sets shows what it
        inherits instead (a variable setting it still wins).
        """
        descriptor = self._repo.descriptor(key)
        choices = descriptor.choices
        if choices is None or not descriptor.is_collection:
            raise ConfigError(f"{key} declares no names", category="invalid")
        current = self._repo.setting_value(descriptor) or {}
        holder = self._repo.holder_value(descriptor)
        defaults = choices.defaults(holder) if choices.defaults is not None else {}
        provenance = self._repo.provenance()
        inherited = self._repo.inherited_value(descriptor, under) if under is not None else None
        below = inherited if isinstance(inherited, dict) else {}
        declared = choices.names()

        def found(name: str) -> tuple[str | None, str, str | None]:
            variable = self._repo.env_var_setting((*descriptor.path, name))
            profile = provenance.get((*descriptor.path, name))
            if variable is None and name in current and profile is None:
                # No profile and no current spelling holds it: an old spelling's variable.
                variable = self._repo.env_var_supplying(descriptor) or "environment"
            if variable is not None and name in current:
                return str(current[name]), variable, variable
            if name in current and profile is not None and profile != under:
                return str(current[name]), _profile_label(profile), None
            if name in below:
                return str(below[name]), _profile_label(DEFAULT_PROFILE), None
            default = defaults.get(name)
            return (None if default is None else str(default)), choices.defaults_label, None

        names = (*declared, *(name for name in current if name not in declared))
        rows = []
        for name in names:
            value, source, variable = found(name)
            rows.append(NamedValue(name, value, source, name in declared, variable))
        return tuple(rows)


@dataclass(frozen=True)
class Ranking:
    """One contract method's ranked providers in an owner's ``extensions``, and its source."""

    contract: str
    """The contract's name within its owner (``repo_source``)."""
    method: str
    providers: tuple[str, ...]
    source: str
    """The profile (``profile:work``) or variable setting it."""
    profile: str | None = None
    """The profile setting it, even when a variable wins over it."""
    variable: str | None = None
    """The variable setting it, which wins over any profile's ranking."""


class ListRankings:
    """The rankings an owner's ``<owner>.extensions`` holds, in file order, with their sources."""

    def __init__(self, repo: SettingsReader) -> None:
        self._repo = repo

    def __call__(self, key: str) -> tuple[Ranking, ...]:
        """The rankings of ``key`` (``workspace.extensions``)."""
        descriptor = self._repo.descriptor(key)
        extensions = self._repo.setting_value(descriptor) or {}
        provenance = self._repo.provenance()
        rankings = []
        for contract, settings in extensions.items():
            for method, providers in settings.rank.items():
                path = (*descriptor.path, contract, "rank", method)
                variable = self._repo.env_var_setting(path)
                profile = provenance.get(path)
                source = variable or (_profile_label(profile) if profile else "default")
                rankings.append(
                    Ranking(contract, method, tuple(providers), source, profile, variable)
                )
        return tuple(rankings)


def _profile_label(profile: str) -> str:
    return Source(kind="profile", profile=profile).label


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
    in :attr:`errors` so the caller can warn about it. Core's section fails
    part by part (``untaped.ui`` apart from ``untaped.http``), its errors
    joined under ``untaped``.
    """

    def __init__(self, repo: SettingsReader) -> None:
        self._repo = repo
        self.errors: dict[str, str] = {}
        self._failed: set[str] = set()

    def __call__(self, *, reveal_secrets: bool = False) -> list[SettingEntry]:
        provenance = self._repo.provenance()
        entries: list[SettingEntry] = []
        for descriptor in self._repo.descriptors():
            section = descriptor.path[0]
            unit = self._repo.validated_with(descriptor)
            current: Any
            if unit in self._failed:
                current = self._repo.raw_setting_value(descriptor)
            else:
                try:
                    current = self._repo.setting_value(descriptor)
                except ConfigError as exc:
                    self._failed.add(unit)
                    earlier = self.errors.get(section)
                    if earlier is None:
                        self.errors[section] = str(exc)
                    elif str(exc) not in earlier.split("; "):
                        self.errors[section] = f"{earlier}; {exc}"
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

    def unvalidated(self, descriptor: FieldDescriptor) -> bool:
        """Whether the last listing showed this leaf raw: what it is validated with failed."""
        return self._repo.validated_with(descriptor) in self._failed


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
            profile, notes = self._current_names(self._repo.profile_data(profile_name) or {})
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
                        note=notes.get(path),
                        **_stability_fields(self._repo, descriptor),
                    )
                )
        return entries

    def _current_names(
        self, profile: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[tuple[str, ...], str]]:
        """``profile`` with old keys at their new names, and a note per renamed leaf."""
        current = dict(profile)
        notes: dict[tuple[str, ...], str] = {}
        for section, data in profile.items():
            model = self._repo.section_model(section)
            if model is None or not isinstance(data, dict):
                continue
            current[section], uses = rename_keys(model, data)
            for use in uses:
                if use.kind == "renamed":
                    path = (section, *use.new.split("."))
                    old = OldSpelling(f"{section}.{use.old}", use.removed_in, section)
                    notes[path] = _deprecated_note(old)
        return current, notes


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
    variable = repo.env_var_supplying(descriptor)
    source = _resolve_source(
        variable,
        _provenance_for(resolved_provenance, descriptor),
        descriptor,
        current,
    )
    old = repo.deprecated_source(descriptor)
    return SettingEntry(
        key=descriptor.key,
        value=display_value(descriptor, current, reveal_secrets=reveal_secrets),
        default=display_default(descriptor, reveal_secrets=reveal_secrets),
        source=source,
        profile=source.profile if include_profile else None,
        note=None if old is None else _deprecated_note(old),
        **_stability_fields(repo, descriptor),
    )


def _stability_fields(repo: SettingsReader, descriptor: FieldDescriptor) -> dict[str, Any]:
    """The entry fields that say how stable ``descriptor`` is, what replaces it and when it goes."""
    mark = repo.mark(descriptor)
    fields: dict[str, Any] = {"stability": stability_name(mark)}
    if isinstance(mark, Deprecated):
        distribution = owner(descriptor.path[0])
        fields |= {
            "use": replacement_text(mark, None),
            "removed_in": mark.removed_in,
            "distribution": distribution,
        }
    return fields


def _deprecated_note(old: OldSpelling) -> str:
    """``from OLD, removed in …``: the old spelling a value was read through, and its date."""
    return f"from {old.shown}, {removal_phrase(removal_text(old.removed_in, owner(old.section)))}"


def _iter_leaves(
    data: dict[str, Any],
    prefix: tuple[str, ...],
    *,
    stop_at: Container[tuple[str, ...]] = (),
) -> list[tuple[tuple[str, ...], Any]]:
    """Yield ``(path, value)`` pairs for every leaf of ``data``.

    A mapping whose path is in ``stop_at`` (a mapping-typed setting such as
    ``untaped.ui.symbols``) is one leaf rather than a subtree.
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
    variable: str | None,
    profile_name: str | None,
    descriptor: FieldDescriptor,
    current: Any,
) -> Source:
    if variable is not None:
        return Source(kind="env", variable=variable)
    if profile_name is not None:
        return Source(kind="profile", profile=profile_name)
    if current is None and not (descriptor.has_default and descriptor.default is not None):
        return Source(kind="unset")
    return Source(kind="default")
