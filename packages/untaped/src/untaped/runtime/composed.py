"""A composed plugin as core's readers see it: its declaration, its settings and its blocks.

Composition's result holds one :class:`ComposedPlugin` per plugin it kept
(core's included, first), so ``config``, ``doctor``, ``setup``, ``plugin
list`` and the contracts registry read every plugin the same way. It is an
accessor over the plugin's :class:`~untaped.runtime.declaration.PluginDecl`,
never a second declaration: what the declaration says is read from
``decl``, how the plugin arrived from ``plugin_ref``. The view adds what
needs the plugin's code, loaded the first time a reader asks (its settings
model, the blocks its class holds, the contracts it offers and the providers
it fills), so a start imports no plugin class. Composition already refused a
class that breaks a rule, so the view checks nothing.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from functools import cache, cached_property
from typing import TYPE_CHECKING, Any, cast

from untaped.runtime.registry import PluginRef, resolve_target
from untaped.sdk._block import OwnedBlock
from untaped.sdk.plugin import held_by
from untaped.sdk.storage.cache import Cache
from untaped.sdk.storage.dir import Dir

if TYPE_CHECKING:
    from cyclopts import App
    from pydantic import BaseModel

    from untaped.runtime.declaration import BlockDecl, CacheDecl, PluginDecl
    from untaped.runtime.mount import Mount
    from untaped.sdk.contracts import Contract
    from untaped.sdk.health.doctor import DoctorCheck
    from untaped.sdk.marks import DeprecatedBehaviour, Stability
    from untaped.sdk.network.credentials import Credential
    from untaped.sdk.storage.holds import ItemStore
    from untaped.sdk.storage.store import Store


@dataclass(frozen=True)
class SkillAsset:
    """The stub skill a plugin ships: core writes it from its own template and
    ``description``, and the stub sends the agent to ``untaped docs``.
    """

    name: str
    description: str


#: The view's member each owned block kind is read through; ``None`` for a kind
#: whose own readers find it (state rows, a lock). A ``Dir`` is a store when it
#: is numbered (its layouts, which references name and doctor lists) or holds
#: ``items=`` (its item store, which holds use).
_MEMBERS: dict[str, str | None] = {
    "cache": "caches",
    "doctor-check": "doctor_checks",
    "credential": "credentials",
    "deprecated-behaviour": "deprecations",
    "state-rows": None,
    "lock": None,
    "dir": "stores",
}


@dataclass(frozen=True, eq=False)
class ComposedPlugin:
    """One composed plugin: ``decl``, how it arrived, and what its code adds, read on first ask."""

    mount: Mount
    decl: PluginDecl
    #: Its settings model, from its own (leaf) module: every start registers it.
    settings: type[BaseModel]
    #: How it arrived: its distribution, entry point and their metadata.
    plugin_ref: PluginRef

    @classmethod
    def of(cls, mount: Mount, name: str, plugin_ref: PluginRef) -> ComposedPlugin:
        """Plugin ``name`` of ``mount``; its settings model is read now (a start needs it)."""
        return cls(mount, mount.declaration.plugin(name), mount.settings[name], plugin_ref)

    @property
    def name(self) -> str:
        """The plugin's name: its config section and its command group."""
        return self.decl.name

    @property
    def root(self) -> bool:
        """Whether its commands mount at the root: core's (its declaration says so)."""
        return self.decl.root

    @cached_property
    def stability(self) -> Stability | None:
        """The plugin's own mark, as the marks machinery reads it."""
        from untaped.runtime.mount import stability_mark  # noqa: PLC0415 - the command line's cost

        return stability_mark(self.decl.stability)

    @property
    def has_commands(self) -> bool:
        """Whether it mounts commands under its name (core's mount at the root)."""
        return not self.decl.root and (bool(self.decl.commands) or self.decl.runs is not None)

    def build_app(self) -> App:
        """Its command group's app, built from the declaration (``has_commands`` only)."""
        from untaped.runtime.mount import build_app  # noqa: PLC0415 - the command line's cost

        return build_app(self.mount, self.name)

    @property
    def skill(self) -> SkillAsset | None:
        """Its stub skill: core's is named after core, a plugin's after the plugin under core's."""
        if not self.decl.skill:
            return None
        return SkillAsset(
            name=self.name if self.decl.root else f"untaped-{self.name}",
            description=self.decl.skill,
        )

    def block_ids(self, member: str) -> tuple[str, ...]:
        """Each block read through ``member`` (``caches``...) as ``<plugin>.<name>``, unimported."""
        return tuple(f"{self.name}.{block.name}" for block in self._declared(member))

    @property
    def caches(self) -> tuple[Cache[Any], ...]:
        return self._member("caches")

    @property
    def doctor_checks(self) -> tuple[DoctorCheck, ...]:
        return self._member("doctor_checks")

    @property
    def credentials(self) -> tuple[Credential[Any], ...]:
        return self._member("credentials")

    @property
    def stores(self) -> tuple[Store | ItemStore[Any], ...]:
        return self._member("stores")

    @property
    def deprecations(self) -> tuple[DeprecatedBehaviour, ...]:
        return self._member("deprecations")

    @cached_property
    def timed_caches(self) -> tuple[Cache[Any], ...]:
        """Each cache with a max age, as its setting reads it, from the declaration alone."""
        return tuple(
            _timed_cache(self.name, block.name, block.cache)
            for block in self._declared("caches")
            if block.cache is not None and block.cache.max_age_seconds is not None
        )

    @property
    def credential_names(self) -> frozenset[str]:
        """Its credentials' names, from the declaration alone."""
        return frozenset(block.name for block in self._declared("credentials"))

    @property
    def owns_contracts(self) -> bool:
        """Whether it offers contracts (read from the declaration; nothing is imported)."""
        return bool(self.decl.offers)

    @cached_property
    def offers(self) -> tuple[type[Contract], ...]:
        """The contracts it offers, imported by name so the plugin class stays unimported.

        A contract made at run time (a test's, in a function) has no importable
        name, so it is read from the plugin class, as its fills always are.
        """
        try:
            return tuple(cast("type[Contract]", resolve_target(each)) for each in self.decl.offers)
        except ImportError, AttributeError:
            return tuple(self.mount.plugins[self.name].offers)

    @cached_property
    def fill_owners(self) -> tuple[str, ...]:
        """The composed plugins whose contracts it fills, in the order of its first fill of each."""
        return tuple(self._fills_by_owner)

    def providers(self, owner: str) -> tuple[Contract, ...]:
        """One instance of each provider class it fills ``owner``'s contracts with.

        The classes are the plugin class's ``fills``, in the declaration's order,
        so a class made at run time (``providing``) needs no importable name and
        two such classes sharing one never stand for each other.
        """
        filled = self.mount.plugins[self.name].fills
        return tuple(filled[index]() for index in self._fills_by_owner.get(owner, ()))

    @cached_property
    def _fills_by_owner(self) -> dict[str, tuple[int, ...]]:
        # a fill no composed plugin owns is left out: a class names no plugin before it is composed
        owners = {
            target: each.name for each in self.mount.declaration.plugins for target in each.offers
        }
        grouped: defaultdict[str, list[int]] = defaultdict(list)
        for index, fill in enumerate(self.decl.fills):
            owner = owners.get(fill.contract)
            if owner is not None:
                grouped[owner].append(index)
        return {owner: tuple(indexes) for owner, indexes in grouped.items()}

    def _declared(self, member: str) -> tuple[BlockDecl, ...]:
        return tuple(
            block
            for block in self.decl.blocks
            if _MEMBERS.get(block.block) == member
            and (block.block != "dir" or block.layout or block.items)
        )

    def _member(self, member: str) -> tuple[Any, ...]:
        # none declared: nothing to import the class for
        return self._held[member] if self._declared(member) else ()

    @cached_property
    def _held(self) -> dict[str, tuple[Any, ...]]:
        """Its owned blocks by member, each given its plugin and name: the class read once."""
        held = held_by(self.mount.plugins[self.name])
        blocks: dict[str, list[Any]] = {
            member: [] for member in _MEMBERS.values() if member is not None
        }
        for decl in self.decl.blocks:
            block = held[decl.attribute]
            member = _MEMBERS.get(decl.block)
            if isinstance(block, Dir):
                spelled = block._spelled_as(self.name, decl.name)
                if block.layout is not None or block.item_type is not None:
                    blocks["stores"].append(
                        spelled._layouts() if block.layout is not None else spelled.items()
                    )
            elif isinstance(block, OwnedBlock) and member is not None:
                blocks[member].append(block._spelled_as(self.name, decl.name))
        return {member: tuple(each) for member, each in blocks.items()}


@cache
def _timed_cache(plugin: str, name: str, declared: CacheDecl) -> Cache[Any]:
    """One declared cache, the same object at every composition, which keys the settings model."""
    assert declared.max_age_seconds is not None  # timed_caches: a cache with a max age only
    return Cache(
        label=declared.label,
        max_age=timedelta(seconds=declared.max_age_seconds),
        renamed_from=declared.renamed_from,
        removed_in=declared.removed_in,
    )._spelled_as(plugin, name)
