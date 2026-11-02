"""Who owns a plugin package, and the deprecated contract methods a plugin's providers fill.

untaped acts on what a plugin declares and on what happens while it runs,
never on its code. What it declares here: the contract methods its
providers fill (:func:`filled_uses`), each deprecated one a :class:`UseDecl`
the declaration keeps for ``doctor`` and lint. What it does is recorded
while a command runs (:mod:`untaped.core.run_uses`). :func:`owner_of` is
the one rule for which package's owner a module is.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from untaped.runtime.declaration import UseDecl
from untaped.sdk.marks import Deprecated, function_mark

if TYPE_CHECKING:
    from untaped.runtime.registry import PluginCandidate


@dataclass(frozen=True)
class Owner:
    """Who a top-level package belongs to: untaped, or a plugin's distribution."""

    distribution: str
    #: The plugin's name; ``untaped`` for core.
    plugin: str
    #: Core's own: a deprecation names its major bare (``14.0``), not the distribution's.
    core: bool = False


def filled_uses(providers: Sequence[type], owners: Mapping[str, Owner]) -> list[UseDecl]:
    """Each deprecated contract method one of ``providers`` fills, as a use of its owner."""
    from untaped.sdk.contracts._declare import contract_of, fills  # noqa: PLC0415 - fills only

    uses: list[UseDecl] = []
    for provider in providers:
        info = contract_of(provider)
        if info is None:
            continue
        owner = owner_of(info.cls.__module__, owners)
        if owner is None:
            continue
        for name, method in info.methods.items():
            mark = function_mark(method.function)
            if isinstance(mark, Deprecated) and fills(provider, info, name):
                where = f"{provider.__module__}:{provider.__qualname__}.{name}"
                uses.append(
                    _use(f"contract {owner.plugin}.{info.name}.{name}", mark, owner, where=where)
                )
    return uses


def owner_of[T](module: str, owners: Mapping[str, T]) -> T | None:
    """Who owns ``module``: the owner of its longest package in ``owners`` (keyed by package).

    A plugin nested in ``untaped`` (``untaped.plugins.acme``) is its own owner,
    not core; of two plugins sharing a namespace package, each owns its own
    subpackage.
    """
    parts = module.split(".")
    for end in range(len(parts), 0, -1):
        found = owners.get(".".join(parts[:end]))
        if found is not None:
            return found
    return None


def owners_of(candidates: Iterable[PluginCandidate], core: str = "untaped") -> dict[str, Owner]:
    """Who each plugin package (its entry point's) belongs to; ``untaped`` is ``core``'s."""
    from untaped.runtime.plugin_package import candidate_package  # noqa: PLC0415 - composing

    owners = {
        package: Owner(each.distribution, each.name)
        for each in candidates
        if each.distribution.strip() and (package := candidate_package(each.target))
    }
    owners["untaped"] = Owner(core, "untaped", core=True)
    return owners


def use_line(distribution: str, version: str, use: UseDecl) -> str:
    """Doctor's words for ``use``: what the plugin uses, and the release that removes it."""
    from untaped.sdk.marks import removal_text  # noqa: PLC0415 - doctor only
    from untaped.sdk.words import removal_phrase  # noqa: PLC0415

    removal = removal_text(use.removed_in, None if use.core else use.distribution)
    head = f"{distribution} {version}".strip()
    where = f"in `{use.where}`" if use.how == "uses" else use.where
    return (
        f"{head} {use.how} {use_name(use)} ({where}), {removal_phrase(removal)}: "
        f"upgrade {distribution} once a release stops using it"
    )


def use_name(use: UseDecl) -> str:
    """The name ``use`` spells, without its kind: ``untaped.sdk.human_size``."""
    return use.id.partition(" ")[2]


def _use(identity: str, mark: Deprecated, owner: Owner, *, where: str) -> UseDecl:
    from untaped.sdk._replacement import sdk_replacement_text  # noqa: PLC0415 - on a use only

    return UseDecl(
        id=identity,
        how="fills",
        distribution=owner.distribution,
        core=owner.core,
        removed_in=mark.removed_in,
        replacement=sdk_replacement_text(mark),
        where=where,
    )


__all__ = [
    "Owner",
    "filled_uses",
    "owner_of",
    "owners_of",
    "use_line",
    "use_name",
]
