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
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from untaped.config_file import read_config_dict
from untaped.contracts._declare import (
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
from untaped.errors import ConfigError
from untaped.plugins.registry import (
    CompositionResult,
    PluginSpec,
    admits,
    owner_requirement,
    owns_contracts,
    range_text,
)
from untaped.profile_resolver import selected_profile
from untaped.records import DuplicateKindError
from untaped.settings import (
    ExtensionSettings,
    active_settings_layout,
    env_var_name,
    load_settings_section,
    registered_profile_model,
)

#: Why a provider is quarantined (closed set, stable surface).
PROVIDER_REASONS = frozenset(
    {
        "owner-not-installed",
        "owner-out-of-range",
        "duplicate-kind",
        "unresolved-item-type",
        "missing-bridge",
        "unserialisable-signature",
        "bad-provider",
    }
)


@dataclass(frozen=True)
class Quarantined:
    """A provider excluded from its owner's contracts, and why."""

    plugin: str
    owner: str
    reason: str
    detail: str
    #: The contract it would fill; ``None`` when its whole group failed to load.
    contract: type[Contract] | None = None

    def __post_init__(self) -> None:
        if self.reason not in PROVIDER_REASONS:
            raise ValueError(f"unknown provider quarantine reason: {self.reason!r}")


@dataclass(frozen=True)
class Provider:
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
    groups: dict[tuple[str, str], tuple[Provider | Quarantined, ...]] = field(default_factory=dict)


_STATE: _State | None = None


def _state() -> _State:
    global _STATE
    from untaped.bootstrap import composition  # noqa: PLC0415 - bootstrap is the CLI's root

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
                state.broken[spec.name] = str(exc) or type(exc).__name__
                continue
            owners.update(dict.fromkeys(declared, spec.name))
        state.owners = owners
    return state.owners


def _declared(spec: PluginSpec) -> tuple[type[Contract], ...]:
    declared = tuple(spec.contracts())
    for each in declared:
        info = contract_of(each)
        if info is None or info.cls is not each:
            raise TypeError(f"it lists {each!r}, which is not a Contract declaration")
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


def offers(contract: ContractInfo) -> list[Provider | Quarantined]:
    """Every provider of ``contract`` and every quarantined offer to it, by plugin name."""
    state = _state()
    owner = owner_of(contract)
    found: list[Provider | Quarantined] = []
    for spec in _plugins(state):
        if owner not in spec.provides:
            continue
        for entry in _group(state, spec, owner):
            target = entry.binding.contract.cls if isinstance(entry, Provider) else entry.contract
            if target is None or target is contract.cls:
                found.append(entry)
    return found


def every_offer() -> list[Provider | Quarantined]:
    """Every provider and quarantined offer in the composition (doctor)."""
    state = _state()
    installed = {spec.name for spec in _plugins(state)}
    _owners(state)
    found: list[Provider | Quarantined] = []
    for spec in _plugins(state):
        for owner in spec.provides:
            if owner in state.broken:
                continue
            if owner not in installed:
                found.append(
                    Quarantined(
                        spec.name,
                        owner,
                        "owner-not-installed",
                        f"{spec.name} provides for {owner}, which is not installed",
                    )
                )
                continue
            found.extend(_group(state, spec, owner))
    return found


def _group(state: _State, spec: PluginSpec, owner: str) -> tuple[Provider | Quarantined, ...]:
    key = (spec.name, owner)
    if key not in state.groups:
        state.groups[key] = _load(state, spec, owner)
    return state.groups[key]


def _load(state: _State, spec: PluginSpec, owner: str) -> tuple[Provider | Quarantined, ...]:
    def whole(reason: str, detail: str) -> tuple[Quarantined, ...]:
        return (Quarantined(spec.name, owner, reason, detail),)

    out_of_range = _out_of_range(state, spec, owner)
    if out_of_range is not None:
        return whole("owner-out-of-range", out_of_range)
    try:
        values = tuple(spec.provides[owner]())
    except DuplicateKindError as exc:
        return whole("duplicate-kind", str(exc))
    except ContractError as exc:
        return whole(exc.reason, str(exc))
    except Exception as exc:
        return whole("bad-provider", f"provides[{owner!r}] of {spec.name} raised: {exc}")
    owned = {cls for cls, name in _owners(state).items() if name == owner}
    entries: list[Provider | Quarantined] = []
    seen: dict[type[Contract], int] = {}
    for value in values:
        entry = _check(spec, owner, owned, value, state.profile)
        if isinstance(entry, Provider):
            contract = entry.binding.contract.cls
            if contract in seen:
                twice = f"{spec.name} provides {contract.__qualname__} twice"
                entries[seen[contract]] = Quarantined(
                    spec.name, owner, "bad-provider", twice, contract
                )
                entry = Quarantined(spec.name, owner, "bad-provider", twice, contract)
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


def _check(
    spec: PluginSpec, owner: str, owned: set[type[Contract]], value: object, profile: str
) -> Provider | Quarantined:
    def quarantined(
        reason: str, detail: str, contract: type[Contract] | None = None
    ) -> Quarantined:
        return Quarantined(spec.name, owner, reason, detail, contract)

    info = contract_of(type(value)) if isinstance(value, Contract) else None
    if info is None or not isinstance(value, Contract) or type(value) is info.cls:
        return quarantined(
            "bad-provider", f"{spec.name} offers {value!r} to {owner}, which is not a provider"
        )
    if info.cls not in owned:
        return quarantined(
            "bad-provider",
            f"{type(value).__qualname__} fills {info.cls.__qualname__}, "
            f"which {owner} does not declare",
        )
    provider = type(value)
    try:
        item = item_type(provider, info)
    except ContractError as exc:
        return quarantined(exc.reason, str(exc), info.cls)
    binding = Binding(spec=spec, owner=owner, contract=info, item=item, profile=profile)
    if not binding.owns_item:
        missing = [
            name
            for name, method in info.methods.items()
            if method.bridge and not fills(provider, info, name)
        ]
        if missing:
            return quarantined(
                "missing-bridge",
                f"{provider.__qualname__} issues {item.__qualname__ if item else '?'} records, "
                f"so it must fill {', '.join(missing)}",
                info.cls,
            )
    wanted = settings_type(provider)
    if wanted is not None and wanted is not spec.settings:
        return quarantined(
            "bad-provider",
            f"{provider.__qualname__} reads Configured[{wanted.__qualname__}], but "
            f"{spec.name}'s settings model is "
            f"{spec.settings.__qualname__ if spec.settings else 'not declared'}",
            info.cls,
        )
    bind(value, binding)
    return Provider(value, binding)


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
        if isinstance(entry, Provider) and fills(type(entry.instance), contract, method)
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


#: The title of a doctor row about contracts: a reason (closed set, stable surface).
DOCTOR_REASONS = frozenset(
    {
        "active",
        "not-configured",
        "unused-method",
        "owner-schema-drift",
        "bad-contracts",
        "rank-unknown-method",
        "rank-not-installed",
        *PROVIDER_REASONS,
    }
)


@dataclass(frozen=True)
class DoctorRow:
    """One doctor row about contracts: a provider's offer, an owner or a ranking."""

    check: Literal["contract-provider", "contracts", "rank"]
    plugin: str
    status: Literal["pass", "warn"]
    #: A :data:`DOCTOR_REASONS` member.
    title: str
    detail: str
    #: The ``untaped`` command (without the program name) that repairs it.
    fix: str | None = None

    def __post_init__(self) -> None:
        if self.title not in DOCTOR_REASONS:
            raise ValueError(f"unknown contract doctor reason: {self.title!r}")


def ready_of(provider: Provider) -> NotReady | None:
    """``provider``'s ``ready()``, a raising one counted as not ready."""
    try:
        return provider.instance.ready()
    except Exception as exc:
        return NotReady(f"ready() raised {type(exc).__name__}: {exc}")


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
            rows.append(DoctorRow("contracts", plugin, "warn", "bad-contracts", detail))
    known = {spec.name for spec in _plugins(state)}
    known |= {record.name for record in state.composition.quarantine}
    owners = owned_contracts()
    for spec in _plugins(state):
        if owns_contracts(spec) and spec.name not in state.broken and wanted(spec.name):
            rows += _rank_rows(spec.name, owners.get(spec.name, []), known)
    return rows


def _offer_row(entry: Provider | Quarantined) -> DoctorRow:
    upgrade = f"upgrade untaped-{entry.plugin}"
    if isinstance(entry, Quarantined):
        name = "" if entry.contract is None else f".{_contract_name(entry.contract)}"
        if entry.reason == "owner-not-installed":
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
        return DoctorRow("contract-provider", entry.plugin, "warn", "unused-method", detail)
    ready = ready_of(entry)
    if ready is not None:
        why = f"waits for {ready.setting}" if ready.setting else f"inactive: {ready.reason}"
        detail = f"{what}: {why}"
        return DoctorRow("contract-provider", entry.plugin, "pass", "not-configured", detail)
    methods = ", ".join(name for name in info.methods if fills(provider, info, name))
    if _drifted(entry):
        detail = (
            f"fills {what}: {methods}; tested against another {what} schema "
            f"than the installed {entry.binding.owner}'s; {upgrade}"
        )
        return DoctorRow("contract-provider", entry.plugin, "warn", "owner-schema-drift", detail)
    detail = f"fills {what}: {methods}"
    return DoctorRow("contract-provider", entry.plugin, "pass", "active", detail)


def _drifted(provider: Provider) -> bool:
    """Whether the owner schema hash the provider recorded differs from the installed owner's."""
    from untaped.contracts._fills import current_hash, recorded_hash  # noqa: PLC0415 - a cycle

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
                rows.append(DoctorRow("rank", owner, "warn", "rank-unknown-method", detail, fix))
                continue
            absent = [plugin for plugin in plugins if plugin not in known]
            if absent:
                kept = [plugin for plugin in plugins if plugin in known]
                detail = (
                    f"{key} ranks {', '.join(absent)}, which {_is_are(absent)} not installed"
                    f"{holder.where}"
                )
                fix = holder.fix(" ".join([clear, *kept]))
                rows.append(DoctorRow("rank", owner, "warn", "rank-not-installed", detail, fix))
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
    provenance = active_settings_layout().resolve(raw, profile=selected_profile(raw)).provenance

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
