"""Testing contracts: compose fakes and prove a provider fills its contract.

Owners compose a fake provider with :func:`compose_with`; providers prove they
fill a contract with :func:`assert_fills`. An owner guards its contracts'
schemas with :func:`untaped.sdk.testing.assert_surface`. Nothing here imports
``untaped.sdk.contracts`` until it is called.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untaped.runtime.contracts.registry import Provider
    from untaped.runtime.registry import CompositionResult, PluginCandidate, PluginSpec
    from untaped.sdk.contracts import Contract

#: The composition an enclosing :func:`compose_with` made, if any.
_ACTIVE: ContextVar[CompositionResult | None] = ContextVar("untaped_compose_with", default=None)


@contextmanager
def compose_with(
    *plugins: str | PluginSpec | PluginCandidate,
    provides: Mapping[str, Sequence[Contract]] | None = None,
) -> Iterator[CompositionResult]:
    """Compose ``plugins`` and fake providers for the block; restore the composition after.

    ``plugins`` are installed plugin names, ``PluginSpec`` objects or
    candidates; with none, every installed plugin is composed.
    ``provides={"fake": [FakeRepos()]}`` adds a plugin named ``fake`` that
    offers each instance to the plugin declaring its contract, so an owner
    tests ``gather`` against a provider it controls (a fake reads no
    ``Configured`` settings). Yields the composition.
    """
    from untaped.runtime import (  # noqa: PLC0415 - the root is heavy; tests pay only on use
        bootstrap,
    )
    from untaped.sdk.contracts._providing import offering_candidates  # noqa: PLC0415

    base = _candidates(plugins)
    fakes = offering_candidates(provides or {}, base)
    with bootstrap.composed_with([*base, *fakes]) as result:
        token = _ACTIVE.set(result)
        try:
            yield result
        finally:
            _ACTIVE.reset(token)


def _candidates(plugins: Sequence[str | PluginSpec | PluginCandidate]) -> list[PluginCandidate]:
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
        elif isinstance(each, PluginSpec):
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
    through ``T`` and, through each ``@bridge`` method, become a valid owner
    record whose ``source`` names the provider and gives the sample back.
    ``samples=`` is required when ``T`` isn't the owner's model. The provider
    is the one an enclosing :func:`compose_with` offers, else the installed
    plugin's. Records the owner's schema hash in the plugin package's
    ``fills.json`` (unless the package is installed in site-packages), which
    ``untaped plugin check`` and ``untaped doctor`` compare with the installed
    owner's (``owner-schema-drift``).
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
    from untaped.runtime.contracts.fills import fills_problems, record_hash  # noqa: PLC0415

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
    record_hash(bound)


def _bound(cls: type) -> Provider:
    from untaped.runtime.contracts.registry import (  # noqa: PLC0415
        Provider,
        every_offer,
        owned_contracts,
        unreadable_owners,
    )
    from untaped.sdk.contracts._declare import contract_of  # noqa: PLC0415

    contract = contract_of(cls)
    entries = every_offer()
    for entry in entries:
        if isinstance(entry, Provider) and type(entry.instance) is cls:
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
        if not isinstance(entry, Provider) and entry.contract in wanted and entry.owner == owner:
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
    raise LookupError(
        f"no installed plugin offers {cls.__qualname__}; compose it with compose_with(...)"
    )
