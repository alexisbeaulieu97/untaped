"""Composing provider instances as a plugin of their own: ``providing``.

An owner that loads providers from somewhere other than an installed
distribution (lint's rule modules in a repository) composes them through
the one path every provider takes: validated, stamped with the plugin they
are offered as, routed by ``gather(plugins=)`` and failure-accounted.
``untaped.sdk.testing.compose_with(provides=)`` builds its fakes the same way.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING

from untaped.sdk.errors import ConfigError

if TYPE_CHECKING:
    from untaped.runtime.registry import CompositionResult, PluginCandidate
    from untaped.sdk.contracts._declare import Contract


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
    not a valid plugin name or is already a candidate's name raises
    :class:`ConfigError` before anything is composed, so a composed plugin
    can never shadow an installed one; a key the composition quarantines (one
    taken by core: ``untaped``, a root command, a config key) raises
    :class:`ConfigError` too, once the composition is restored.
    Composing is refused inside a contract call, as every composition is.
    """
    from untaped.runtime import bootstrap  # noqa: PLC0415 - the root is heavy; pay only on use
    from untaped.runtime.registry import (  # noqa: PLC0415
        check_plugin_name,
        discover_candidates,
    )

    base = list(discover_candidates()) if candidates is None else list(candidates)
    held = {candidate.name for candidate in base}
    for key in offered:
        check_plugin_name(key)
        if key in held:
            raise ConfigError(f"{key!r} is the name of an installed plugin; pick another name")
    built = offering_candidates(offered, base, owner=owner)
    with bootstrap.composed_with([*base, *built]) as result:
        refused = [record for record in result.quarantine if record.name in offered]
        if refused:
            raise ConfigError(
                "; ".join(f"{record.name}: {record.reason}: {record.detail}" for record in refused)
            )
        yield result


def offering_candidates(
    offered: Mapping[str, Sequence[Contract]],
    base: Sequence[PluginCandidate],
    *,
    owner: str | None = None,
) -> list[PluginCandidate]:
    """One candidate per key of ``offered``, offering its instances to their contracts' owners.

    Each instance's owner is the composed plugin declaring its contract,
    among ``base`` (composed once); with ``owner`` set, every instance must
    fill one of that plugin's contracts.
    """
    from untaped.runtime.bootstrap import SHELL_SPEC  # noqa: PLC0415
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
    for plugin in compose(SHELL_SPEC, base).plugins:
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
            if owner is not None and owners[info.cls] != owner:
                raise LookupError(
                    f"{info.cls.__qualname__} is {owners[info.cls]}'s contract, not {owner}'s"
                )
            grouped.setdefault(owners[info.cls], []).append(instance)
        provides = {each: _offer(tuple(group)) for each, group in grouped.items()}
        spec = PluginSpec(name=name, provides=provides)
        built.append(PluginCandidate(distribution=f"untaped-{name}", name=name, target=spec))
    return built


def _offer(instances: tuple[Contract, ...]) -> Callable[[], Sequence[Contract]]:
    def offer() -> tuple[Contract, ...]:
        return instances

    return offer
