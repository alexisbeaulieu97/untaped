"""Testing contracts: compose fakes, prove a provider fills its contract, snapshot schemas.

Owners compose a fake provider with :func:`compose_with` and snapshot each
contract's schemas with :func:`assert_contract_schemas`; providers prove they
fill a contract with :func:`assert_fills`. Nothing here imports
``untaped.contracts`` until it is called.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untaped.contracts import Contract
    from untaped.contracts._registry import Provider
    from untaped.plugins.registry import CompositionResult, PluginCandidate, PluginSpec

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
    from untaped import bootstrap  # noqa: PLC0415 - the root is heavy; tests pay only on use

    base = _candidates(plugins)
    fakes = [_fake(name, list(offered), base) for name, offered in (provides or {}).items()]
    with bootstrap.composed_with([*base, *fakes]) as result:
        token = _ACTIVE.set(result)
        try:
            yield result
        finally:
            _ACTIVE.reset(token)


def _candidates(plugins: Sequence[str | PluginSpec | PluginCandidate]) -> list[PluginCandidate]:
    from untaped.plugins.registry import (  # noqa: PLC0415
        PluginCandidate,
        PluginSpec,
        discover_candidates,
    )
    from untaped.testing import plugin_candidate  # noqa: PLC0415

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


def _fake(name: str, offered: list[Contract], base: Sequence[PluginCandidate]) -> PluginCandidate:
    """A candidate for plugin ``name`` offering ``offered`` to their contracts' owners."""
    from untaped.bootstrap import SHELL_SPEC  # noqa: PLC0415
    from untaped.contracts._declare import contract_of  # noqa: PLC0415
    from untaped.plugins.registry import PluginSpec, compose, owns_contracts  # noqa: PLC0415
    from untaped.testing import plugin_candidate  # noqa: PLC0415

    owners: dict[type, str] = {}
    for plugin in compose(SHELL_SPEC, base).plugins:
        if owns_contracts(plugin.spec):
            owners.update(dict.fromkeys(plugin.spec.contracts(), plugin.spec.name))
    grouped: dict[str, list[Contract]] = {}
    for instance in offered:
        info = contract_of(type(instance))
        if info is None or info.cls is type(instance):
            raise TypeError(f"{instance!r} is not a provider (an instance of a contract subclass)")
        if info.cls not in owners:
            raise LookupError(f"no composed plugin declares {info.cls.__qualname__}")
        grouped.setdefault(owners[info.cls], []).append(instance)
    provides = {owner: _offer(tuple(group)) for owner, group in grouped.items()}
    return plugin_candidate(PluginSpec(name=name, provides=provides))


def _offer(instances: tuple[Contract, ...]) -> Callable[[], Sequence[Contract]]:
    def offer() -> tuple[Contract, ...]:
        return instances

    return offer


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
    from untaped.contracts._declare import contract_of  # noqa: PLC0415

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
    from untaped.contracts._fills import fills_problems, record_hash  # noqa: PLC0415

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
    from untaped.contracts._declare import contract_of  # noqa: PLC0415
    from untaped.contracts._registry import (  # noqa: PLC0415
        Provider,
        every_offer,
        owned_contracts,
        unreadable_owners,
    )

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
    from untaped.conventions import candidate_package  # noqa: PLC0415
    from untaped.plugins.registry import PluginSpec, discover_candidates  # noqa: PLC0415

    top = cls.__module__.partition(".")[0]
    installed = {candidate.name: candidate for candidate in discover_candidates()}
    for candidate in installed.values():
        package = candidate_package(candidate.target)
        if package is None or package.partition(".")[0] != top:
            continue
        spec = _resolve(candidate.target)
        if isinstance(spec, PluginSpec):
            return [candidate.name, *(owner for owner in spec.provides if owner in installed)]
    raise LookupError(
        f"no installed plugin offers {cls.__qualname__}; compose it with compose_with(...)"
    )


def _resolve(target: object) -> object:
    if not isinstance(target, str):
        return target
    from importlib import import_module  # noqa: PLC0415

    module, _, attribute = target.partition(":")
    return getattr(import_module(module), attribute, None)


def assert_contract_schemas(*contracts: type[Contract], snapshots: Path) -> None:
    """Fail when a contract's schemas changed incompatibly since ``snapshots`` recorded them.

    Writes ``<contract>.json`` (each method's parameter and return schemas,
    and its stability) under ``snapshots`` when it is missing, and rewrites it
    after a compatible change: a new method, a new optional parameter or
    field, a removed experimental method. A removed or retyped field, a new
    required field or parameter, a changed constraint or a removed stable
    method fails, listing each change; to accept one deliberately (a major
    release), delete the snapshot and run again.
    """
    from untaped.contracts._declare import contract_of  # noqa: PLC0415
    from untaped.contracts._schema import contract_schema, schema_changes  # noqa: PLC0415

    failures: list[str] = []
    for contract in contracts:
        info = contract_of(contract)
        if info is None or info.cls is not contract:
            raise TypeError(f"{contract!r} is not a contract declaration")
        path = snapshots / f"{info.name}.json"
        current = contract_schema(info)
        try:
            recorded = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            recorded = None
        if recorded is not None:
            changes = schema_changes(recorded, current)
            if changes:
                failures += [f"  {info.name}.{change}" for change in changes]
                continue
            if recorded == current:
                continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert not failures, (
        "incompatible contract changes (delete the snapshot to accept them in a major release):\n"
        + "\n".join(failures)
    )
