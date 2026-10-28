"""Testing contracts: compose fakes and prove a provider fills its contract.

Owners compose a fake provider with :func:`compose_with`; providers prove they
fill a contract with :func:`assert_fills`. An owner guards its contracts'
schemas with :func:`untaped.sdk.testing.assert_surface`. Nothing here imports
``untaped.sdk.contracts`` until it is called.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untaped.runtime.contracts.registry import BoundProvider
    from untaped.runtime.registry import CompositionResult, PluginCandidate, PluginSpec
    from untaped.sdk.contracts import Contract
    from untaped.sdk.plugin import Plugin

#: The composition an enclosing :func:`compose_with` made, if any.
_ACTIVE: ContextVar[CompositionResult | None] = ContextVar("untaped_compose_with", default=None)


@contextmanager
def compose_with(
    *plugins: str | PluginSpec | type[Plugin] | PluginCandidate,
    provides: Mapping[str, Sequence[Contract]] | None = None,
) -> Iterator[CompositionResult]:
    """Compose ``plugins`` and fake providers for the block; restore the composition after.

    ``plugins`` are installed plugin names, ``PluginSpec`` objects, plugin
    classes or candidates; with none, every installed plugin is composed.
    ``provides={"fake": [FakeRepos()]}`` adds a plugin named ``fake`` that
    offers each instance to the plugin declaring its contract, so an owner
    tests ``gather`` against a provider it controls (a fake reads no
    ``Configured`` settings). The block runs under a run of that composition,
    as a command of it would. Yields the composition.
    """
    from untaped.runtime import (  # noqa: PLC0415 - the root is heavy; tests pay only on use
        bootstrap,
    )
    from untaped.runtime.run import build_run, entered  # noqa: PLC0415

    base = _candidates(plugins)
    fakes = _offering_candidates(provides or {}, base)
    with (
        bootstrap.composed_with([*base, *fakes]) as result,
        entered(build_run(result.mount.declaration, result.mount.settings)),
    ):
        token = _ACTIVE.set(result)
        try:
            yield result
        finally:
            _ACTIVE.reset(token)


def _offering_candidates(
    offered: Mapping[str, Sequence[Contract]], base: Sequence[PluginCandidate]
) -> list[PluginCandidate]:
    """One candidate per key of ``offered``, offering its instances to their contracts' owners.

    Each instance's owner is the composed plugin declaring its contract,
    among ``base`` (composed once).
    """
    from untaped.runtime.registry import (  # noqa: PLC0415
        PluginCandidate,
        PluginSpec,
        compose,
        owns_contracts,
    )
    from untaped.sdk.contracts._declare import contract_of  # noqa: PLC0415

    if not offered:
        return []
    owners: dict[type, str] = {}
    for plugin in compose(base).plugins:
        if owns_contracts(plugin.spec):
            owners.update(dict.fromkeys(plugin.spec.contracts(), plugin.spec.name))
    built: list[PluginCandidate] = []
    for name, instances in offered.items():
        grouped: dict[str, list[Contract]] = {}
        for instance in instances:
            info = contract_of(type(instance))
            if info is None or info.cls is type(instance):
                raise TypeError(
                    f"{instance!r} is not a provider (an instance of a contract subclass)"
                )
            if info.cls not in owners:
                raise LookupError(f"no composed plugin declares {info.cls.__qualname__}")
            grouped.setdefault(owners[info.cls], []).append(instance)
        provides = {each: _offer(tuple(group)) for each, group in grouped.items()}
        spec = PluginSpec(name=name, provides=provides)
        built.append(PluginCandidate(distribution=f"untaped-{name}", name=name, target=spec))
    return built


def _offer(instances: tuple[Contract, ...]) -> Callable[[], Sequence[Contract]]:
    def offer() -> tuple[Contract, ...]:
        return instances

    return offer


def _candidates(
    plugins: Sequence[str | PluginSpec | type[Plugin] | PluginCandidate],
) -> list[PluginCandidate]:
    from untaped.runtime.registry import (  # noqa: PLC0415
        PluginCandidate,
        PluginSpec,
        discover_candidates,
    )
    from untaped.sdk.testing import plugin_candidate  # noqa: PLC0415

    if not plugins:
        return list(discover_candidates())
    installed: dict[str, PluginCandidate] | None = None
    found: list[PluginCandidate] = []
    for each in plugins:
        if isinstance(each, PluginCandidate):
            found.append(each)
        elif isinstance(each, PluginSpec | type):
            found.append(plugin_candidate(each))
        else:
            if installed is None:
                installed = {candidate.name: candidate for candidate in discover_candidates()}
            if each not in installed:
                raise LookupError(f"no installed plugin named {each!r}")
            found.append(installed[each])
    return found


def assert_fills(provider: type[Contract] | Contract, *, samples: Sequence[object] = ()) -> None:
    """Fail unless ``provider`` fills its contract for every sample of its record type ``T``.

    Each sample (a ``T`` or a mapping of one) must survive a JSON round trip
    through ``T`` and, through the bridge (the method taking ``T`` and
    returning the owner's model), become a valid owner record whose
    ``source`` names the provider and gives the sample back.
    ``samples=`` is required when ``T`` isn't the owner's model. The provider
    is the one an enclosing :func:`compose_with` offers, else the installed
    plugin's.
    """
    from untaped.sdk.contracts._declare import contract_of  # noqa: PLC0415

    cls = provider if isinstance(provider, type) else type(provider)
    info = contract_of(cls)
    if info is None or info.cls is cls:
        raise TypeError(f"{cls.__qualname__} is not a provider (a subclass of a contract)")
    if _ACTIVE.get() is not None:
        _assert_bound(cls, samples)
        return
    with compose_with(*_installed_for(cls)):
        _assert_bound(cls, samples)


def _assert_bound(cls: type, samples: Sequence[object]) -> None:
    from untaped.runtime.contracts.fills import fills_problems  # noqa: PLC0415

    bound = _bound(cls)
    if not bound.binding.owns_item and not samples:
        item = bound.binding.item
        raise TypeError(
            f"{cls.__qualname__} issues {item.__qualname__ if item else 'its own'} records, "
            "so assert_fills needs samples=[...] of them"
        )
    problems = fills_problems(bound, samples)
    assert not problems, f"{cls.__qualname__} doesn't fill {bound.binding.contract.name}:\n" + (
        "\n".join(f"  {line}" for line in problems)
    )


def _bound(cls: type) -> BoundProvider:
    from untaped.runtime.contracts.registry import (  # noqa: PLC0415
        BoundProvider,
        every_offer,
        owned_contracts,
        unreadable_owners,
    )
    from untaped.sdk.contracts._declare import contract_of  # noqa: PLC0415

    contract = contract_of(cls)
    entries = every_offer()
    for entry in entries:
        if isinstance(entry, BoundProvider) and type(entry.instance) is cls:
            return entry
    owner = next((o for o, infos in owned_contracts().items() if contract in infos), None)
    if owner is None:
        broken = unreadable_owners()
        raise LookupError(
            f"{cls.__qualname__} fills {getattr(contract, 'name', contract)}, whose owner "
            + (
                f"may be {', '.join(broken)}: its contracts function fails; see doctor"
                if broken
                else "isn't composed; compose it too: compose_with(owner, plugin)"
            )
        )
    for entry in entries:
        wanted = (None, getattr(contract, "cls", None))
        if (
            not isinstance(entry, BoundProvider)
            and entry.contract in wanted
            and entry.owner == owner
        ):
            raise AssertionError(
                f"{entry.plugin}'s offer to {entry.owner} is quarantined ({entry.reason}): "
                f"{entry.detail}"
            )
    raise LookupError(f"no composed plugin offers {cls.__qualname__}")


def _installed_for(cls: type) -> list[str]:
    """The installed plugin offering ``cls`` and the installed owners it provides for."""
    from untaped.runtime.plugin_package import candidate_package  # noqa: PLC0415
    from untaped.runtime.registry import (  # noqa: PLC0415
        PluginSpec,
        discover_candidates,
        resolve_target,
    )
    from untaped.sdk.plugin import Plugin  # noqa: PLC0415

    top = cls.__module__.partition(".")[0]
    installed = {candidate.name: candidate for candidate in discover_candidates()}
    for candidate in installed.values():
        package = candidate_package(candidate.target)
        if package is None or package.partition(".")[0] != top:
            continue
        try:
            spec = resolve_target(candidate.target)
        except ImportError, AttributeError:
            continue  # a target that does not resolve offers nothing
        if isinstance(spec, PluginSpec):
            return [candidate.name, *(owner for owner in spec.provides if owner in installed)]
        if isinstance(spec, type) and issubclass(spec, Plugin):
            return [candidate.name, *_offering(installed.values(), spec.fills)]
    raise LookupError(
        f"no installed plugin offers {cls.__qualname__}; compose it with compose_with(...)"
    )


def _offering(candidates: Iterable[PluginCandidate], fills: Sequence[type]) -> list[str]:
    """The installed plugin classes offering a contract one of ``fills`` fills, by name."""
    from untaped.runtime.registry import resolve_target  # noqa: PLC0415
    from untaped.sdk.contracts._declare import contract_of  # noqa: PLC0415
    from untaped.sdk.plugin import Plugin  # noqa: PLC0415

    wanted = {info.cls for each in fills if (info := contract_of(each)) is not None}
    found: list[str] = []
    for candidate in candidates:
        try:
            plugin = resolve_target(candidate.target)
        except ImportError, AttributeError:
            continue
        if not (isinstance(plugin, type) and issubclass(plugin, Plugin)):
            continue
        if wanted & set(plugin.offers):
            found.append(candidate.name)
    return found
