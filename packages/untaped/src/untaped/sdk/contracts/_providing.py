"""Composing provider classes as a plugin of their own: ``providing``.

An owner that loads providers from somewhere other than an installed
distribution (lint's rule modules in a repository) composes them through
the one path every provider takes: a plugin whose ``fills`` are the
classes, validated, stamped with the plugin they are offered as, routed by
``gather(plugins=)`` and failure-accounted. The testing kit's
``install(providers=)`` builds its plugins the same way.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, cast

from untaped.sdk.errors import ConfigError

if TYPE_CHECKING:
    from pydantic import BaseModel

    from untaped.runtime.registry import CompositionResult, PluginCandidate
    from untaped.sdk.plugin import Plugin


@contextmanager
def providing(
    offered: Mapping[str, Sequence[type]],
    *,
    candidates: Sequence[PluginCandidate] | None = None,
) -> Iterator[CompositionResult]:
    """Compose the installed plugins plus one plugin per key of ``offered``; restore after.

    ``providing({"house": (HouseRules,)})`` composes a plugin named
    ``house`` whose ``fills`` are those provider classes, each filling the
    contract it subclasses (its owner read from the contract, as ``fills``
    reads it), so ``gather`` asks it like any provider and its answers'
    ``plugin`` is ``house``. ``candidates`` replaces entry-point discovery.
    A class that is no provider raises :class:`TypeError` naming it; a key
    that is not a valid plugin name or is already a candidate's name raises
    :class:`ConfigError` before anything is composed, so a composed plugin
    can never shadow an installed one; a key the composition quarantines (one
    taken by core: ``untaped``, a root command, a config key) raises
    :class:`ConfigError` too, once the composition is restored. The plugin
    lives in a package of its own (``untaped_providing_<key>``), so it never
    takes over the package, and the kinds, of the plugin defining its classes.
    A class whose contract no composed plugin offers is not asked.
    Composing is refused inside a contract call, as every composition is.
    """
    from untaped.runtime import bootstrap  # noqa: PLC0415 - the root is heavy; pay only on use
    from untaped.runtime.registry import (  # noqa: PLC0415
        PluginCandidate,
        check_plugin_name,
        discover_candidates,
    )

    base = list(discover_candidates()) if candidates is None else list(candidates)
    held = {candidate.name for candidate in base}
    for key in offered:
        check_plugin_name(key)
        if key in held:
            raise ConfigError(f"{key!r} is the name of an installed plugin; pick another name")
    built = [
        PluginCandidate(
            distribution=f"untaped-{key}",
            name=key,
            target=providers_plugin(
                key, classes, module=f"untaped_providing_{key.replace('-', '_')}"
            ),
        )
        for key, classes in offered.items()
    ]
    with bootstrap.composed_with([*base, *built]) as result:
        refused = [record for record in result.quarantine if record.name in offered]
        if refused:
            raise ConfigError(
                "; ".join(f"{record.name}: {record.reason}: {record.detail}" for record in refused)
            )
        yield result


#: The empty settings model of each name and module :func:`providers_plugin` made a plugin of:
#: composing that name again (a test installing twice) is the same section.
_EMPTY: dict[tuple[str, str], type[BaseModel]] = {}


def providers_plugin(
    name: str, classes: Sequence[type], *, module: str | None = None
) -> type[Plugin]:
    """A plugin named ``name`` whose ``fills`` are the provider ``classes``.

    It lives in ``module`` (by default the first class's, as a test's
    providers do). Its settings are the classes' ``Provider[S]`` (one model
    for all of them), else an empty model, the same one each time ``name``
    is made. A class that is no provider of a contract, two settings
    models, or no class at all raise :class:`TypeError`.
    """
    from pydantic import ConfigDict, create_model  # noqa: PLC0415

    from untaped.runtime.registry import check_plugin_name  # noqa: PLC0415
    from untaped.sdk.contracts._declare import (  # noqa: PLC0415
        Provider,
        contract_of,
        settings_type,
    )
    from untaped.sdk.plugin import Plugin  # noqa: PLC0415

    check_plugin_name(name)
    for each in classes:
        info = contract_of(each) if isinstance(each, type) else None
        if info is None or info.cls is each or not issubclass(each, Provider):
            raise TypeError(
                f"providers[{name!r}] holds {each!r}, not a provider: subclass the contract and "
                "Provider (Provider[S] when it reads settings)"
            )
    if not classes:
        raise TypeError(f"providers[{name!r}] holds no provider class")
    title = name.title().replace("-", "")
    read = {settings_type(each) for each in classes} - {None}
    if len(read) > 1:
        names = sorted(each.__qualname__ for each in read if each is not None)
        raise TypeError(
            f"providers[{name!r}] read different settings ({', '.join(names)}); "
            "a plugin has one settings model"
        )
    model = next(iter(read), None)
    home = module or classes[0].__module__
    if model is None:
        # Made in the plugin's module, so its package is the providers', not core's.
        if (name, home) not in _EMPTY:
            _EMPTY[name, home] = create_model(
                f"{title}Settings", __config__=ConfigDict(frozen=True), __module__=home
            )
        model = _EMPTY[name, home]
    plugin = type(
        title,
        (Plugin,),
        {
            "__doc__": f"The providers {name}.",
            "__module__": home,
            "settings": model,
            "fills": tuple(classes),
        },
    )
    return cast("type[Plugin]", plugin)
