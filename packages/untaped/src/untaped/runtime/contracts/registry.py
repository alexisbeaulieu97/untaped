"""Who owns each contract and who fills it, for the current composition.

Owners are found through ``PluginSpec.contracts``; providers through
``PluginSpec.provides[owner]``, whose function runs once per process and
profile, on the first ask for one of that owner's contracts. A provider that
breaks a rule is quarantined for its own offer only: the plugin's commands,
settings and other offers are untouched. An owner whose ``contracts`` breaks
loses its own contracts only; doctor names it.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal, cast

from untaped.runtime.config_file import read_config_dict
from untaped.runtime.profile_resolver import selected_profile
from untaped.runtime.registry import (
    CompositionResult,
    DeclaredBlocks,
    PluginSpec,
    admits,
    owner_requirement,
    owns_contracts,
    range_text,
)
from untaped.runtime.settings import (
    ExtensionSettings,
    active_settings_layout,
    env_var_name,
    load_settings_section,
    registered_profile_model,
)
from untaped.sdk.contracts._declare import (
    Binding,
    Contract,
    ContractError,
    ContractInfo,
    NotReady,
    bind,
    contract_of,
    fills,
    item_type,
    settings_type,
    unused_methods,
)
from untaped.sdk.contracts._reasons import DoctorReason, QuarantineReason, Reason
from untaped.sdk.errors import ConfigError
from untaped.sdk.fields import SettingKey
from untaped.sdk.records import DuplicateKindError, KindError, kind_of

if TYPE_CHECKING:
    from pydantic import BaseModel

    from untaped.sdk.storage.cache import Cache

_LOG = logging.getLogger("untaped.contracts")


def upgrade_hint(plugin: str) -> str:
    """The hint for a defect in ``plugin``'s own offer: a newer release may fix it."""
    return f"upgrade untaped-{plugin}"


@dataclass(frozen=True)
class Quarantined:
    """A provider excluded from its owner's contracts, and why."""

    plugin: str
    owner: str
    reason: QuarantineReason
    detail: str
    #: The contract it would fill; ``None`` when its whole group failed to load.
    contract: type[Contract] | None = None


@dataclass(frozen=True)
class BoundProvider:
    """A bound provider instance ready to be asked."""

    instance: Contract
    binding: Binding

    @property
    def plugin(self) -> str:
        return self.binding.plugin


@dataclass
class _State:
    composition: CompositionResult
    profile: str
    owners: dict[type[Contract], str] | None = None
    #: Why an owner's ``contracts`` couldn't be read, by plugin.
    broken: dict[str, str] = field(default_factory=dict)
    groups: dict[tuple[str, str], tuple[BoundProvider | Quarantined, ...]] = field(
        default_factory=dict
    )


_STATE: _State | None = None


def _state() -> _State:
    global _STATE
    from untaped.runtime.bootstrap import composition  # noqa: PLC0415 - bootstrap is the CLI's root

    current = composition()
    profile = selected_profile()
    if _STATE is None or _STATE.composition is not current or _STATE.profile != profile:
        _STATE = _State(current, profile)
    return _STATE


def reset() -> None:
    """Forget every loaded provider (tests; a new composition does it by itself)."""
    global _STATE
    _STATE = None


def _plugins(state: _State) -> list[PluginSpec]:
    return [plugin.spec for plugin in state.composition.plugins]


def _owners(state: _State) -> dict[type[Contract], str]:
    if state.owners is None:
        owners: dict[type[Contract], str] = {}
        for spec in _plugins(state):
            try:
                declared = _declared(spec)
            except Exception as exc:
                _LOG.debug("%s's contracts raised %s", spec.name, type(exc).__name__, exc_info=exc)
                state.broken[spec.name] = f"{type(exc).__name__}: {exc}"
                continue
            owners.update(dict.fromkeys(declared, spec.name))
        state.owners = owners
    return state.owners


def _listed(spec: PluginSpec, cache: Cache[Any]) -> bool:
    """Whether ``spec`` lists a cache under the name ``@cached`` gave ``cache``.

    A 12.0 plugin's caches are read by id, so its class stays unimported.
    """
    if cache._held_as is not None:
        return True
    caches = spec.caches
    if isinstance(caches, DeclaredBlocks):
        ids = list(caches.ids)
    else:
        ids = [each.qualified for each in caches if each.spelled]
    return f"{spec.name}.{cache._declared_as}" in ids


def _declared(spec: PluginSpec) -> tuple[type[Contract], ...]:
    declared = tuple(spec.contracts())
    for each in declared:
        info = contract_of(each)
        if info is None or info.cls is not each:
            raise TypeError(f"it lists {each!r}, which is not a Contract declaration")
        for method in info.methods.values():
            cache = method.cache
            # a listed cache is the plugin's own (composition quarantines a foreign one); one
            # @cached made is listed under its name (a 12.0 plugin's declaration lists it)
            if cache is not None and not cache.spelled and not _listed(spec, cache):
                raise TypeError(
                    f"{info.cls.__qualname__}.{method.name} is cached in a cache with no "
                    f"plugin and name: spell it Cache({spec.name!r}, "
                    f"'{info.name}_{method.name}', ...) and list it on PluginSpec.caches"
                )
            if cache is not None and cache.spelled and cache not in spec.caches:
                raise TypeError(
                    f"{info.cls.__qualname__}.{method.name} is cached in {cache.qualified}, "
                    f"which is not one of {spec.name}'s caches (list it on PluginSpec.caches)"
                )
    return declared


def owner_of(contract: ContractInfo) -> str:
    """The plugin that declares ``contract`` (``ConfigError`` when no installed plugin does)."""
    state = _state()
    owner = _owners(state).get(contract.cls)
    if owner is None:
        unread = "".join(
            f"; {plugin}'s contracts couldn't be read: {why}"
            for plugin, why in state.broken.items()
        )
        raise ConfigError(
            f"no installed plugin declares the contract {contract.cls.__qualname__} "
            f"(list it in its PluginSpec.contracts){unread}"
        )
    return owner


def offers(contract: ContractInfo) -> list[BoundProvider | Quarantined]:
    """Every provider of ``contract`` and every quarantined offer to it, by plugin name."""
    state = _state()
    owner = owner_of(contract)
    found: list[BoundProvider | Quarantined] = []
    for spec in _plugins(state):
        if owner not in spec.provides:
            continue
        for entry in _group(state, spec, owner):
            target = (
                entry.binding.contract.cls if isinstance(entry, BoundProvider) else entry.contract
            )
            if target is None or target is contract.cls:
                found.append(entry)
    return found


def every_offer() -> list[BoundProvider | Quarantined]:
    """Every provider and quarantined offer in the composition (doctor)."""
    state = _state()
    installed = {spec.name for spec in _plugins(state)}
    _owners(state)
    found: list[BoundProvider | Quarantined] = []
    for spec in _plugins(state):
        for owner in spec.provides:
            if owner in state.broken:
                continue
            if owner not in installed:
                found.append(
                    Quarantined(
                        spec.name,
                        owner,
                        Reason.owner_not_installed,
                        f"{spec.name} provides for {owner}, which is not installed",
                    )
                )
                continue
            found.extend(_group(state, spec, owner))
    return found


def _group(state: _State, spec: PluginSpec, owner: str) -> tuple[BoundProvider | Quarantined, ...]:
    key = (spec.name, owner)
    if key not in state.groups:
        state.groups[key] = _load(state, spec, owner)
    return state.groups[key]


def _load(state: _State, spec: PluginSpec, owner: str) -> tuple[BoundProvider | Quarantined, ...]:
    def whole(reason: QuarantineReason, detail: str) -> tuple[Quarantined, ...]:
        return (Quarantined(spec.name, owner, reason, detail),)

    out_of_range = _out_of_range(state, spec, owner)
    if out_of_range is not None:
        return whole(Reason.owner_out_of_range, out_of_range)
    try:
        values = tuple(spec.provides[owner]())
    except DuplicateKindError as exc:
        return whole(Reason.duplicate_kind, str(exc))
    except KindError as exc:
        return whole(Reason.bad_provider, str(exc))
    except ContractError as exc:
        return whole(_reason(exc.reason), str(exc))
    except Exception as exc:
        _LOG.debug(
            "provides[%r] of %s raised %s", owner, spec.name, type(exc).__name__, exc_info=exc
        )
        detail = f"{type(exc).__name__}: {exc}"
        return whole(Reason.bad_provider, f"provides[{owner!r}] of {spec.name} raised {detail}")
    owned = {cls for cls, name in _owners(state).items() if name == owner}
    entries: list[BoundProvider | Quarantined] = []
    seen: dict[type[Contract], int] = {}
    for value in values:
        entry = _check(spec, owner, owned, value, state.profile, _kinds(state))
        if isinstance(entry, BoundProvider):
            contract = entry.binding.contract.cls
            if contract in seen:
                twice = f"{spec.name} provides {contract.__qualname__} twice"
                entries[seen[contract]] = Quarantined(
                    spec.name, owner, Reason.bad_provider, twice, contract
                )
                entry = Quarantined(spec.name, owner, Reason.bad_provider, twice, contract)
            else:
                seen[contract] = len(entries)
        entries.append(entry)
    return tuple(entries)


def _out_of_range(state: _State, spec: PluginSpec, owner: str) -> str | None:
    """Why the installed ``owner`` is outside the range ``spec`` declares for it, if it is.

    The range is the one the provider's ``<owner>`` extra requires; without
    one, or without the owner's version, nothing is checked (the
    ``provides-requirement`` convention asks for the range).
    """
    refs = {plugin.spec.name: plugin.plugin_ref for plugin in state.composition.plugins}
    provider, installed = refs.get(spec.name), refs.get(owner)
    if provider is None or installed is None or not installed.distribution_version:
        return None
    requirement = owner_requirement(provider.requires_dist, owner, installed.distribution)
    if requirement is None or admits(requirement, installed.distribution_version):
        return None
    return (
        f"{spec.name} requires {requirement.name}{range_text(requirement)} for {owner}, "
        f"but {installed.distribution_version} is installed"
    )


def _kinds(state: _State) -> Callable[[type[BaseModel]], str]:
    """Each model's kind as the composition gave it, else its 11.x ``kind=`` (``""``: none).

    Read from the composition the registry holds, not the run, so a first ask
    from a thread that doesn't see the run binds the same kinds.
    """
    mount = state.composition.mount
    return lambda model: mount.kind_of(model) or kind_of(model) or ""


def _check(
    spec: PluginSpec,
    owner: str,
    owned: set[type[Contract]],
    value: object,
    profile: str,
    kinds: Callable[[type[BaseModel]], str],
) -> BoundProvider | Quarantined:
    def quarantined(
        reason: QuarantineReason, detail: str, contract: type[Contract] | None = None
    ) -> Quarantined:
        return Quarantined(spec.name, owner, reason, detail, contract)

    info = contract_of(type(value)) if isinstance(value, Contract) else None
    if info is None or not isinstance(value, Contract) or type(value) is info.cls:
        return quarantined(
            Reason.bad_provider, f"{spec.name} offers {value!r} to {owner}, which is not a provider"
        )
    if info.cls not in owned:
        return quarantined(
            Reason.bad_provider,
            f"{type(value).__qualname__} fills {info.cls.__qualname__}, "
            f"which {owner} does not declare",
        )
    provider = type(value)
    try:
        item = item_type(provider, info)
    except ContractError as exc:
        return quarantined(_reason(exc.reason), str(exc), info.cls)
    binding = Binding(
        spec=spec,
        owner=owner,
        contract=info,
        item=item,
        profile=profile,
        item_kind="" if item is None else kinds(item),
        owner_kind="" if info.item is None else kinds(info.item),
    )
    if not binding.owns_item and item is not None and not binding.item_kind:
        return quarantined(
            Reason.unresolved_item_type,
            f"{provider.__qualname__}: {item.__qualname__} has no kind, "
            "so a piped record can't find its way back to it",
            info.cls,
        )
    if not binding.owns_item:
        missing = [
            name
            for name, method in info.methods.items()
            if method.bridge and not fills(provider, info, name)
        ]
        if missing:
            return quarantined(
                Reason.missing_bridge,
                f"{provider.__qualname__} issues {item.__qualname__ if item else '?'} records, "
                f"so it must fill {', '.join(missing)}",
                info.cls,
            )
    wanted = settings_type(provider)
    if wanted is not None and wanted is not spec.settings:
        return quarantined(
            Reason.bad_provider,
            f"{provider.__qualname__} reads Provider[{wanted.__qualname__}], but "
            f"{spec.name}'s settings model is "
            f"{spec.settings.__qualname__ if spec.settings else 'not declared'}",
            info.cls,
        )
    bind(value, binding)
    return BoundProvider(value, binding)


def owned_contracts() -> dict[str, list[ContractInfo]]:
    """Every installed owner's readable contracts, by owner, each in declaration order."""
    owned: dict[str, list[ContractInfo]] = {}
    for cls, owner in _owners(_state()).items():
        info = contract_of(cls)
        if info is not None:
            owned.setdefault(owner, []).append(info)
    return owned


def unreadable_owners() -> list[str]:
    """The installed owners whose ``contracts`` function failed, by name."""
    state = _state()
    _owners(state)
    return sorted(state.broken)


def method_providers(contract: ContractInfo, method: str, order: Sequence[str]) -> list[str]:
    """The plugins whose usable providers fill ``contract.method``, as ``gather`` asks them.

    Ranked plugins come first, in ``order``, then the rest by name.
    """
    names = [
        entry.plugin
        for entry in offers(contract)
        if isinstance(entry, BoundProvider) and fills(type(entry.instance), contract, method)
    ]
    return sorted(
        names, key=lambda name: (name not in order, order.index(name) if name in order else 0, name)
    )


def rankings(owner: str) -> Mapping[str, ExtensionSettings]:
    """``owner``'s ``extensions`` settings in the selected profile, by contract name.

    Only the owner's own section is read, so a broken sibling section never
    blocks an ask; a broken owner section raises its ``ConfigError``.
    """
    if registered_profile_model(owner) is None:
        return {}
    section = load_settings_section(owner)
    extensions: Mapping[str, ExtensionSettings] = getattr(section, "extensions", {})
    return extensions


def ranking(owner: str, contract: str, method: str) -> tuple[str, ...]:
    """The plugins ranked for ``owner.contract.method``, first first.

    An entry for a contract or method that doesn't exist is never asked for,
    so it changes nothing; doctor reports it (``rank-unknown-method``).
    """
    extension = rankings(owner).get(contract)
    return () if extension is None else tuple(extension.rank.get(method, ()))


def _contract_name(contract: type[Contract]) -> str:
    info = contract_of(contract)
    return "" if info is None else info.name


@dataclass(frozen=True)
class DoctorRow:
    """One doctor row about contracts: a provider's offer, an owner or a ranking."""

    check: Literal["contract-provider", "contracts", "rank"]
    plugin: str
    status: Literal["pass", "warn"]
    title: DoctorReason
    detail: str
    #: The ``untaped`` command (without the program name) that repairs it.
    fix: str | None = None


def ready_of(provider: BoundProvider) -> NotReady | None:
    """``provider``'s ``ready()``, its setting spelled in full; a raising one is not ready.

    ``key(S).field`` is spelled under the section of the plugin whose
    settings model ``S`` is (the owner rule), whoever runs the provider; a
    model no composed plugin owns is spelled under the provider's plugin.
    """
    try:
        ready = provider.instance.ready()
    except Exception as exc:
        _LOG.debug("%s's ready() raised %s", provider.plugin, type(exc).__name__, exc_info=exc)
        return NotReady(f"ready() raised {type(exc).__name__}: {exc}")
    if ready is None or ready.setting is None:
        return ready
    spelled = replace(ready)  # ``spelled`` is core's to set, so no provider passes it
    object.__setattr__(spelled, "spelled", f"{_section(provider, ready.setting)}.{ready.setting}")
    return spelled


def _section(provider: BoundProvider, setting: SettingKey) -> str:
    from untaped.sdk._run import run_or_none  # noqa: PLC0415 - the run imports the registry

    run = run_or_none()
    if run is not None:
        try:
            return run.composition.owner_of_settings(SettingKey.model_of(setting))
        except LookupError:
            pass
    return provider.plugin


def _reason(word: str) -> QuarantineReason:
    """A declaration error's reason word as the quarantine reason it is."""
    return cast("QuarantineReason", Reason(word))


def doctor_rows(plugins: frozenset[str] | None = None) -> list[DoctorRow]:
    """A row per provider offer, per owner whose contracts can't be read, per bad ranking.

    An inactive provider (not configured, or waiting for an owner that isn't
    installed) is a pass row; a quarantined offer, a method the contract no
    longer has, a provider tested against another owner schema than the
    installed one (``owner-schema-drift``) and a ranking naming a missing
    contract, method or plugin warn.
    ``plugins`` limits the rows to those plugins (a provider's, or an owner's).
    """

    def wanted(name: str) -> bool:
        return plugins is None or name in plugins

    rows = [_offer_row(entry) for entry in every_offer() if wanted(entry.plugin)]
    state = _state()
    for plugin, why in state.broken.items():
        if wanted(plugin):
            detail = f"contracts couldn't be read: {why}"
            rows.append(DoctorRow("contracts", plugin, "warn", Reason.bad_contracts, detail))
    known = {spec.name for spec in _plugins(state)}
    known |= {record.name for record in state.composition.quarantine}
    owners = owned_contracts()
    for spec in _plugins(state):
        if owns_contracts(spec) and spec.name not in state.broken and wanted(spec.name):
            rows += _rank_rows(spec.name, owners.get(spec.name, []), known)
    return rows


def _offer_row(entry: BoundProvider | Quarantined) -> DoctorRow:
    upgrade = upgrade_hint(entry.plugin)
    if isinstance(entry, Quarantined):
        name = "" if entry.contract is None else f".{_contract_name(entry.contract)}"
        if entry.reason == Reason.owner_not_installed:
            detail = f"waits for {entry.owner} (not installed)"
            return DoctorRow("contract-provider", entry.plugin, "pass", entry.reason, detail)
        detail = f"{entry.owner}{name}: {entry.detail}; {upgrade}"
        return DoctorRow("contract-provider", entry.plugin, "warn", entry.reason, detail)
    info = entry.binding.contract
    what = f"{entry.binding.owner}.{info.name}"
    provider = type(entry.instance)
    unused = unused_methods(provider, info)
    if unused:
        detail = f"{what}: {', '.join(unused)} (not in {info.name}; never called); {upgrade}"
        return DoctorRow("contract-provider", entry.plugin, "warn", Reason.unused_method, detail)
    ready = ready_of(entry)
    if ready is not None:
        why = f"waits for {ready.spelled}" if ready.spelled else f"inactive: {ready.reason}"
        detail = f"{what}: {why}"
        return DoctorRow("contract-provider", entry.plugin, "pass", Reason.not_configured, detail)
    methods = ", ".join(name for name in info.methods if fills(provider, info, name))
    if _drifted(entry):
        detail = (
            f"fills {what}: {methods}; tested against another {what} schema "
            f"than the installed {entry.binding.owner}'s; {upgrade}"
        )
        return DoctorRow(
            "contract-provider", entry.plugin, "warn", Reason.owner_schema_drift, detail
        )
    detail = f"fills {what}: {methods}"
    return DoctorRow("contract-provider", entry.plugin, "pass", Reason.active, detail)


def _drifted(provider: BoundProvider) -> bool:
    """Whether the owner schema hash the provider recorded differs from the installed owner's."""
    from untaped.runtime.contracts.fills import (  # noqa: PLC0415 - a cycle
        current_hash,
        recorded_hash,
    )

    recorded = recorded_hash(provider)
    return recorded is not None and recorded != current_hash(provider)


def _rank_rows(owner: str, contracts: list[ContractInfo], known: set[str]) -> list[DoctorRow]:
    """Rows for rankings doctor can't follow; ``known`` holds installed and quarantined plugins.

    A quarantined plugin stays in its ranking: it works again once upgraded.
    """
    try:
        extensions = rankings(owner)
    except ConfigError:
        return []  # the owner's settings row already fails
    declared = {info.name: info for info in contracts}
    holders = _holders()
    rows: list[DoctorRow] = []
    for contract, extension in extensions.items():
        info = declared.get(contract)
        for method, plugins in extension.rank.items():
            path = (owner, "extensions", contract, "rank", method)
            key = ".".join(path)
            holder = holders(path)
            clear = f"plugin rank {owner}.{contract} {method}"
            if info is None or method not in info.methods or info.methods[method].bridge:
                missing = (
                    f"declares no contract {contract}"
                    if info is None
                    else f"declares no method {contract}.{method}"
                    if method not in info.methods
                    else f"asks no one for {contract}.{method} (a bridge)"
                )
                detail = f"{key}: {owner} {missing}; the ranking is ignored{holder.where}"
                fix = holder.fix(clear)
                rows.append(
                    DoctorRow("rank", owner, "warn", Reason.rank_unknown_method, detail, fix)
                )
                continue
            absent = [plugin for plugin in plugins if plugin not in known]
            if absent:
                kept = [plugin for plugin in plugins if plugin in known]
                detail = (
                    f"{key} ranks {', '.join(absent)}, which {_is_are(absent)} not installed"
                    f"{holder.where}"
                )
                fix = holder.fix(" ".join([clear, *kept]))
                rows.append(
                    DoctorRow("rank", owner, "warn", Reason.rank_not_installed, detail, fix)
                )
    return rows


@dataclass(frozen=True)
class _Holder:
    """Where a ranking comes from: the profile whose own data holds it, or the environment."""

    profile: str | None
    env: str | None

    @property
    def where(self) -> str:
        return f" (set by {self.env})" if self.env else ""

    def fix(self, command: str) -> str | None:
        """``command`` aimed at the holding profile; none when the environment sets it."""
        if self.profile is None:
            return None
        return f"--profile {self.profile} {command}"


def _holders() -> Callable[[tuple[str, ...]], _Holder]:
    try:
        raw = read_config_dict()
    except ConfigError:
        raw = {}
    provenance = active_settings_layout().resolve(raw).provenance

    environ = {name.upper(): value for name, value in os.environ.items()}

    def holder(path: tuple[str, ...]) -> _Holder:
        for depth in range(len(path), 0, -1):
            name = env_var_name(path[:depth])
            if name in environ and _holds(environ[name], path[depth:]):
                return _Holder(None, name)
        return _Holder(provenance.get(path), None)

    return holder


def _holds(value: str, rest: tuple[str, ...]) -> bool:
    """Whether an environment ``value`` sets the setting ``rest`` below its own (JSON)."""
    if not rest:
        return True
    try:
        cursor: object = json.loads(value)
    except ValueError:
        return False
    for part in rest:
        if not isinstance(cursor, dict) or part not in cursor:
            return False
        cursor = cursor[part]
    return True


def _is_are(names: list[str]) -> str:
    return "is" if len(names) == 1 else "are"
