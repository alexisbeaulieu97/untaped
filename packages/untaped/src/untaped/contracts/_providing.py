"""Composing provider instances as a plugin of their own: ``providing``.

An owner that loads providers from somewhere other than an installed
distribution (lint's rule modules in a repository) composes them through
the one path every provider takes: validated, stamped with the plugin they
are offered as, routed by ``gather(plugins=)`` and failure-accounted.
``untaped.testing.compose_with(provides=)`` builds its fakes the same way.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING

from untaped.errors import ConfigError

if TYPE_CHECKING:
    from untaped.contracts._declare import Contract
    from untaped.plugins.registry import CompositionResult, PluginCandidate

#: The shell's own prefix: no plugin, real or composed, may be called this.
_RESERVED = "untaped"


@contextmanager
def providing(
    owner: str,
    offered: Mapping[str, Sequence[Contract]],
    *,
    candidates: Sequence[PluginCandidate] | None = None,
) -> Iterator[CompositionResult]:
    """Compose the installed plugins plus one plugin per key of ``offered``; restore after.

    ``providing("lint", {"house": [HouseRules()]})`` composes a plugin named
    ``house`` offering each instance to ``owner``'s contract it subclasses,
    so ``gather`` asks it like any provider and its answers' ``plugin`` is
    ``house``. ``candidates`` replaces entry-point discovery. A key that is
    ``untaped``, is not a valid plugin name or is already a candidate's name
    raises :class:`ConfigError` before anything is composed, so a composed
    plugin can never shadow (or quarantine) an installed one. Composing is
    refused inside a contract call, as every composition is.
    """
    from untaped import bootstrap  # noqa: PLC0415 - the root is heavy; pay only on use
    from untaped.plugins.registry import check_plugin_name, discover_candidates  # noqa: PLC0415

    base = list(discover_candidates()) if candidates is None else list(candidates)
    held = {candidate.name for candidate in base}
    for key in offered:
        if key == _RESERVED:
            raise ConfigError(f"{key!r} is reserved for untaped itself; pick another name")
        check_plugin_name(key)
        if key in held:
            raise ConfigError(f"{key!r} is the name of an installed plugin; pick another name")
    built = [
        offering_candidate(key, list(instances), base, owner=owner)
        for key, instances in offered.items()
    ]
    with bootstrap.composed_with([*base, *built], quiet=True) as result:
        yield result


def offering_candidate(
    name: str,
    offered: Sequence[Contract],
    base: Sequence[PluginCandidate],
    *,
    owner: str | None = None,
) -> PluginCandidate:
    """A candidate for plugin ``name`` offering ``offered`` to their contracts' owners.

    Each instance's owner is the composed plugin declaring its contract,
    among ``base``; with ``owner`` set, every instance must fill one of that
    plugin's contracts.
    """
    from untaped.bootstrap import SHELL_SPEC  # noqa: PLC0415
    from untaped.contracts._declare import contract_of  # noqa: PLC0415
    from untaped.plugins.registry import (  # noqa: PLC0415
        PluginCandidate,
        PluginSpec,
        compose,
        owns_contracts,
    )

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
        if owner is not None and owners[info.cls] != owner:
            raise LookupError(
                f"{info.cls.__qualname__} is {owners[info.cls]}'s contract, not {owner}'s"
            )
        grouped.setdefault(owners[info.cls], []).append(instance)
    provides = {each: _offer(tuple(group)) for each, group in grouped.items()}
    spec = PluginSpec(name=name, provides=provides)
    return PluginCandidate(distribution=f"untaped-{name}", name=name, target=spec)


def _offer(instances: tuple[Contract, ...]) -> Callable[[], Sequence[Contract]]:
    def offer() -> tuple[Contract, ...]:
        return instances

    return offer
