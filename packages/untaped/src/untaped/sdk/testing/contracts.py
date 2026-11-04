"""Testing contracts: compose fakes and prove a provider fills its contract.

Owners compose a fake provider with :func:`compose_with`; providers prove they
fill a contract with :func:`assert_fills`, and read a row of theirs as an
owner's ``Stdin(via=bridge)`` reads it with :func:`piped_through`. An owner guards its contracts'
schemas with :func:`untaped.sdk.testing.assert_surface`. Nothing here imports
``untaped.sdk.contracts`` until it is called.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from untaped.runtime.contracts.registry import BoundProvider
    from untaped.runtime.registry import CompositionResult, PluginCandidate
    from untaped.sdk.contracts import Contract
    from untaped.sdk.plugin import Plugin

#: The composition an enclosing :func:`compose_with` made, if any.
_ACTIVE: ContextVar[CompositionResult | None] = ContextVar("untaped_compose_with", default=None)


@contextmanager
def compose_with(
    *plugins: str | type[Plugin] | PluginCandidate,
) -> Iterator[CompositionResult]:
    """Compose ``plugins`` for the block; restore the composition after.

    ``plugins`` are installed plugin names, plugin classes or candidates;
    with none, every installed plugin is composed. An owner tests ``gather``
    against a provider it controls by composing a plugin class whose
    ``fills`` holds a fake provider (a fake reads no ``Configured``
    settings). The block runs under a run of that composition, as a command
    of it would. Yields the composition.
    """
    from untaped.runtime import (  # noqa: PLC0415 - the root is heavy; tests pay only on use
        bootstrap,
    )
    from untaped.runtime.run import build_run, entered  # noqa: PLC0415

    with (
        bootstrap.composed_with(_candidates(plugins)) as result,
        entered(build_run(result.mount.declaration, result.mount.settings, result.mount.plugins)),
    ):
        token = _ACTIVE.set(result)
        try:
            yield result
        finally:
            _ACTIVE.reset(token)


def _candidates(plugins: Sequence[str | type[Plugin] | PluginCandidate]) -> list[PluginCandidate]:
    from untaped.runtime.registry import PluginCandidate, discover_candidates  # noqa: PLC0415
    from untaped.sdk.testing import plugin_candidate  # noqa: PLC0415

    if not plugins:
        return list(discover_candidates())
    installed: dict[str, PluginCandidate] | None = None
    found: list[PluginCandidate] = []
    for each in plugins:
        if isinstance(each, PluginCandidate):
            found.append(each)
        elif isinstance(each, type):
            found.append(plugin_candidate(each))
        else:
            if installed is None:
                installed = {candidate.name: candidate for candidate in discover_candidates()}
            if each not in installed:
                raise LookupError(f"no installed plugin named {each!r}")
            found.append(installed[each])
    return found


def assert_fills(provider: type[Contract] | Contract, *, samples: Sequence[object] = ()) -> None:
    """Fail unless ``provider`` fills its contract for every sample of the records it issues from.

    Each sample (a ``T``, a mapping of one, or a record one of the
    provider's record bridges takes) must survive a JSON round trip through
    its model and, through each bridge taking it (the item bridge for ``T``,
    a record bridge for the record its fill names), become a valid issued
    record whose ``source`` names the provider and reads back as the sample
    through ``own``. ``samples=`` is required when ``T`` isn't the owner's
    model, and holds a sample of the record each record bridge it fills
    takes. The provider is the one an enclosing :func:`compose_with`
    offers, else the installed plugin's.
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


def piped_through[R](bridge: Callable[..., R], row: BaseModel) -> R:
    """``row`` as a command's ``Stdin(via=bridge)`` reads it when it is piped in.

    The row travels as its kind, as on the wire, and comes back as the
    bridge's model: through the bridge of the provider whose fill takes it
    (its ``T`` for the item bridge, the record a record bridge's fill names),
    with that provider as its ``source``, or as it is when it is that
    model's own kind. The errors are the ones the command would exit with.
    """
    from untaped.sdk._pipe import PipeEnvelope  # noqa: PLC0415
    from untaped.sdk._rows import _as_row  # noqa: PLC0415 - the pipe writer's dump
    from untaped.sdk.contracts._select import bridged  # noqa: PLC0415
    from untaped.sdk.records import kind_of  # noqa: PLC0415

    envelope = PipeEnvelope(kind=kind_of(type(row)), record=_as_row(row), lineno=1)
    return bridged(bridge, envelope, type(row))


def _assert_bound(cls: type, samples: Sequence[object]) -> None:
    from untaped.runtime.contracts.fills import fills_problems  # noqa: PLC0415

    bound = _bound(cls)
    if not bound.binding.owns_item and not samples:
        item = bound.binding.item
        raise TypeError(
            f"{cls.__qualname__} issues {item.__qualname__ if item else 'its own'} records, "
            "so assert_fills needs samples=[...] of them"
        )
    _record_samples(bound, samples)
    problems = fills_problems(bound, samples)
    assert not problems, f"{cls.__qualname__} doesn't fill {bound.binding.contract.name}:\n" + (
        "\n".join(f"  {line}" for line in problems)
    )


def _record_samples(bound: BoundProvider, samples: Sequence[object]) -> None:
    """Each record bridge the provider fills has a sample of the record it takes."""
    from untaped.sdk.contracts._declare import bridge_takes  # noqa: PLC0415

    cls = type(bound.instance)
    info = bound.binding.contract
    for name, method in info.methods.items():
        if not method.bridge or method.item_bridge:
            continue
        takes = bridge_takes(cls, info, name)
        if takes is not None and not any(isinstance(sample, takes) for sample in samples):
            raise TypeError(
                f"{cls.__qualname__} fills {name}, which takes {takes.__qualname__} records, "
                f"so assert_fills needs a {takes.__qualname__} among samples=[...]"
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
    refused = [entry for entry in entries if not isinstance(entry, BoundProvider)]
    wanted = (None, getattr(contract, "cls", None))
    # its own refusal first; else one the registry couldn't tie to a class
    for entry in sorted(refused, key=lambda each: each.provider is not cls):
        if entry.provider not in (None, cls):
            continue
        if entry.contract in wanted and entry.owner == owner:
            raise AssertionError(
                f"{entry.plugin}'s offer to {entry.owner} is quarantined ({entry.reason}): "
                f"{entry.described}"
            )
    raise LookupError(f"no composed plugin offers {cls.__qualname__}")


def _installed_for(cls: type) -> list[str]:
    """The installed plugin offering ``cls`` and the installed owners it provides for."""
    from untaped.runtime.plugin_package import candidate_package  # noqa: PLC0415
    from untaped.runtime.registry import discover_candidates, resolve_target  # noqa: PLC0415
    from untaped.sdk.plugin import Plugin  # noqa: PLC0415

    top = cls.__module__.partition(".")[0]
    installed = {candidate.name: candidate for candidate in discover_candidates()}
    for candidate in installed.values():
        package = candidate_package(candidate.target)
        if package is None or package.partition(".")[0] != top:
            continue
        try:
            found = resolve_target(candidate.target)
        except ImportError, AttributeError:
            continue  # a target that does not resolve offers nothing
        if isinstance(found, type) and issubclass(found, Plugin):
            return [candidate.name, *_offering(installed.values(), found.fills)]
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
