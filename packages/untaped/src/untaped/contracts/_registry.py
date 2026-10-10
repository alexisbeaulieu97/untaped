"""Who owns each contract and who fills it, for the current composition.

Owners are found through ``PluginSpec.contracts``; providers through
``PluginSpec.provides[owner]``, whose function runs once per process and
profile, on the first ask for one of that owner's contracts. A provider that
breaks a rule is quarantined for its own offer only: the plugin's commands,
settings and other offers are untouched. An owner whose ``contracts`` breaks
loses its own contracts only; doctor names it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

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
from untaped.plugins.registry import CompositionResult, PluginSpec, owns_contracts
from untaped.profile_resolver import selected_profile
from untaped.records import DuplicateKindError
from untaped.settings import ExtensionSettings, load_settings_section, registered_profile_model

#: Why a provider is quarantined (closed set, stable surface).
PROVIDER_REASONS = frozenset(
    {
        "owner-not-installed",
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


@dataclass(frozen=True)
class DoctorRow:
    """One doctor row about contracts: a provider's offer, an owner or a ranking."""

    check: str
    plugin: str
    status: str
    #: A closed reason: ``active``, ``not-configured``, a quarantine reason,
    #: ``unused-method``, ``bad-contracts``, ``rank-unknown-method`` or
    #: ``rank-not-installed``.
    title: str
    detail: str
    #: The ``untaped`` command (without the program name) that repairs it.
    fix: str | None = None


def doctor_rows() -> list[DoctorRow]:
    """A row per provider offer, per owner whose contracts can't be read, per bad ranking.

    An inactive provider (not configured, or waiting for an owner that isn't
    installed) is a pass row; a quarantined offer, a method the contract no
    longer has and a ranking naming a missing contract, method or plugin warn.
    """
    rows = [_offer_row(entry) for entry in every_offer()]
    state = _state()
    for plugin, why in state.broken.items():
        detail = f"contracts couldn't be read: {why}"
        rows.append(DoctorRow("contracts", plugin, "warn", "bad-contracts", detail))
    installed = {spec.name for spec in _plugins(state)}
    owners = owned_contracts()
    for spec in _plugins(state):
        if owns_contracts(spec) and spec.name not in state.broken:
            rows += _rank_rows(spec.name, owners.get(spec.name, []), installed)
    return rows


def _offer_row(entry: Provider | Quarantined) -> DoctorRow:
    if isinstance(entry, Quarantined):
        name = "" if entry.contract is None else f".{_contract_name(entry.contract)}"
        if entry.reason == "owner-not-installed":
            detail = f"waits for {entry.owner} (not installed)"
            return DoctorRow("contract-provider", entry.plugin, "pass", entry.reason, detail)
        detail = f"{entry.owner}{name}: {entry.detail}; upgrade untaped-{entry.plugin}"
        return DoctorRow("contract-provider", entry.plugin, "warn", entry.reason, detail)
    info = entry.binding.contract
    what = f"{entry.binding.owner}.{info.name}"
    provider = type(entry.instance)
    unused = unused_methods(provider, info)
    if unused:
        detail = f"{what}: {', '.join(unused)} (not in {info.name}; never called)"
        return DoctorRow("contract-provider", entry.plugin, "warn", "unused-method", detail)
    try:
        ready = entry.instance.ready()
    except Exception as exc:
        ready = NotReady(f"ready() raised {type(exc).__name__}: {exc}")
    if ready is not None:
        why = f"waits for {ready.setting}" if ready.setting else f"inactive: {ready.reason}"
        detail = f"{what}: {why}"
        return DoctorRow("contract-provider", entry.plugin, "pass", "not-configured", detail)
    methods = ", ".join(name for name in info.methods if fills(provider, info, name))
    detail = f"fills {what}: {methods}"
    return DoctorRow("contract-provider", entry.plugin, "pass", "active", detail)


def _rank_rows(owner: str, contracts: list[ContractInfo], installed: set[str]) -> list[DoctorRow]:
    try:
        extensions = rankings(owner)
    except ConfigError:
        return []  # the owner's settings row already fails
    declared = {info.name: info for info in contracts}
    rows: list[DoctorRow] = []
    for contract, extension in extensions.items():
        info = declared.get(contract)
        for method, plugins in extension.rank.items():
            key = f"{owner}.extensions.{contract}.rank.{method}"
            clear = f"plugin rank {owner}.{contract} {method}"
            if info is None or method not in info.methods or info.methods[method].bridge:
                missing = (
                    f"declares no contract {contract}"
                    if info is None
                    else f"declares no method {contract}.{method}"
                    if method not in info.methods
                    else f"asks no one for {contract}.{method} (a bridge)"
                )
                detail = f"{key}: {owner} {missing}; the ranking is ignored"
                rows.append(DoctorRow("rank", owner, "warn", "rank-unknown-method", detail, clear))
                continue
            absent = [plugin for plugin in plugins if plugin not in installed]
            if absent:
                kept = [plugin for plugin in plugins if plugin in installed]
                detail = f"{key} ranks {', '.join(absent)}, which {_is_are(absent)} not installed"
                fix = " ".join([clear, *kept])
                rows.append(DoctorRow("rank", owner, "warn", "rank-not-installed", detail, fix))
    return rows


def _is_are(names: list[str]) -> str:
    return "is" if len(names) == 1 else "are"
