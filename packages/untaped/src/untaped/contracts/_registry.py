"""Who owns each contract and who fills it, for the current composition.

Owners are found through ``PluginSpec.contracts``; providers through
``PluginSpec.provides[owner]``, whose function runs once per process and
profile, on the first ask for one of that owner's contracts. A provider that
breaks a rule is quarantined for its own offer only: the plugin's commands,
settings and other offers are untouched. An owner whose ``contracts`` breaks
loses its own contracts only; doctor names it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from untaped.contracts._declare import (
    Binding,
    Contract,
    ContractError,
    ContractInfo,
    bind,
    contract_of,
    fills,
    item_type,
    settings_type,
    unused_methods,
)
from untaped.errors import ConfigError
from untaped.plugins.registry import CompositionResult, DoctorResult, PluginContext, PluginSpec
from untaped.profile_resolver import selected_profile
from untaped.records import DuplicateKindError

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


def ranking(owner: str, contract: str, method: str) -> tuple[str, ...]:
    """The plugins ranked for ``owner.contract.method``, first first.

    The ``extensions`` settings that hold rankings arrive with ``untaped rank``;
    until then nothing is ranked.
    """
    del owner, contract, method
    return ()


@dataclass(frozen=True)
class OfferReport:
    """One offer as doctor reports it."""

    plugin: str
    owner: str
    contract: str
    #: The quarantine reason, or ``unused-method``; ``None`` for a clean offer.
    reason: str | None = None
    detail: str = ""


def offer_reports() -> list[OfferReport]:
    """Each offer with its problem: a quarantine, or methods the contract no longer has."""
    reports: list[OfferReport] = []
    for entry in every_offer():
        if isinstance(entry, Quarantined):
            name = "" if entry.contract is None else _contract_name(entry.contract)
            reports.append(OfferReport(entry.plugin, entry.owner, name, entry.reason, entry.detail))
            continue
        info = entry.binding.contract
        unused = unused_methods(type(entry.instance), info)
        if unused:
            detail = f"{', '.join(unused)} (not in {info.name}; never called)"
            reports.append(
                OfferReport(entry.plugin, entry.binding.owner, info.name, "unused-method", detail)
            )
        else:
            reports.append(OfferReport(entry.plugin, entry.binding.owner, info.name))
    return reports


def _contract_name(contract: type[Contract]) -> str:
    info = contract_of(contract)
    return "" if info is None else info.name


#: The doctor row reporting contract providers.
DOCTOR_CHECK_ID = "contract-providers"


def doctor_row(_context: PluginContext) -> DoctorResult:
    """A ``warn`` naming each quarantined offer, unused method and unreadable owner, else a pass.

    An offer to an owner that isn't installed is no problem: it waits for that owner.
    """
    try:
        reports = offer_reports()
    except ConfigError as exc:
        return DoctorResult(id=DOCTOR_CHECK_ID, ok=False, detail=str(exc))
    problems = [
        f"{plugin}'s contracts couldn't be read: {why}" for plugin, why in _state().broken.items()
    ]
    problems += [
        f"{report.plugin} for {report.owner}.{report.contract or '?'}: "
        f"{report.reason}: {report.detail}"
        for report in reports
        if report.reason not in {None, "owner-not-installed"}
    ]
    if problems:
        return DoctorResult(id=DOCTOR_CHECK_ID, ok=True, warn=True, detail="; ".join(problems))
    usable = sum(report.reason is None for report in reports)
    waiting = sorted({report.owner for report in reports if report.reason is not None})
    parts = [f"{usable} provider(s), all usable"] if usable else []
    if waiting:
        parts.append(f"offers wait for {', '.join(waiting)} (not installed)")
    detail = "; ".join(parts) or "no plugin fills a contract"
    return DoctorResult(id=DOCTOR_CHECK_ID, ok=True, detail=detail)
