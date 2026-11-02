"""Composition's walk of one plugin class: everything it reaches, named and checked.

:class:`PluginWalk` follows a plugin class's references (commands,
groups, blocks, fills, caches) and collects the declarations and problems
:func:`untaped.runtime.composition.compose` settles across plugins.
"""

from __future__ import annotations

import itertools
import sys
import typing
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from untaped.runtime.command_walk import CommandWalk, is_deprecated, mark_stability
from untaped.runtime.declaration import (
    BlockDecl,
    CacheDecl,
    CommandDecl,
    FillDecl,
    GroupDecl,
    GuideDecl,
    OptionDecl,
    PluginDecl,
    PointDecl,
    Problem,
    StabilityDecl,
    TypeDecl,
    target_of,
)
from untaped.runtime.describe import (
    fields_of,
    first_paragraph,
    method_decl,
    models_in,
    prose,
)
from untaped.sdk._block import Block, OwnedBlock
from untaped.sdk._names import kebab, snake
from untaped.sdk.marks import (
    COMMAND_TEXT,
    class_mark,
    stability_refusal,
)
from untaped.sdk.plugin import (
    RESERVED_NAMES,
    Command,
    Group,
    Plugin,
    StubSkill,
    held_by,
)
from untaped.sdk.storage.cache import Cache
from untaped.sdk.storage.dir import Dir

if TYPE_CHECKING:
    from untaped.runtime.composition import PluginEntry
    from untaped.sdk.contracts._declare import Contract, ContractInfo


def package_of(target: str) -> str:
    """The top-level package of an entry point's module: every class under it is the plugin's."""
    return target.partition(":")[0].partition(".")[0]


def cache_setting(cache: Cache[Any]) -> CacheDecl:
    max_age = cache.default_max_age
    return CacheDecl(
        label=cache.label,
        max_age_seconds=None if max_age is None else int(max_age.total_seconds()),
        renamed_from=cache.renamed_from,
        removed_in=cache.removed_in,
    )


def point_stability_of(mark: object) -> StabilityDecl | None:
    """A contract's mark: its replacement is another contract, named by its target."""
    decl = mark_stability(mark)
    if decl is None or decl.replaced_by is None:
        return decl
    return decl.model_copy(update={"replacement": decl.replaced_by, "replaced_by": None})


class _Replacements:
    """A plugin's deprecation replacements: a command class spelled as its line, text checked.

    A mark names its replacement command by class (``replacement=Set``), which
    must be a command of the plugin that stays, deprecated neither itself nor
    with its group or plugin, and is spelled ``untaped svc set`` for help,
    the warning, docs and the surface. Another plugin's command is named as
    text, so text naming one of the plugin's own commands is a problem: the
    class survives a rename.
    """

    def __init__(
        self,
        walk: PluginWalk,
        commands: tuple[CommandDecl | GroupDecl, ...],
        runs: CommandDecl | None,
        plugin: StabilityDecl | None,
    ) -> None:
        self.walk = walk
        self.root = walk.root
        gone = f"deprecated with {walk.name}" if is_deprecated(plugin) else None
        #: Each command or group by target, and why it goes when it does; the first path wins.
        self.own: dict[str, tuple[CommandDecl | GroupDecl, str | None]] = {}
        self._index(commands, gone)
        if runs is not None:
            self._add(runs, gone)
        #: Each line text may spell a command by, aliases included, and its target.
        self.lines: dict[str, str] = {}
        for target, (node, _) in self.own.items():
            line = self._line(node)
            self.lines.setdefault(line, target)
            for alias in node.aliases if isinstance(node, CommandDecl) else ():
                self.lines.setdefault(f"{line.rpartition(' ')[0]} {alias.name}", target)

    def _add(self, node: CommandDecl | GroupDecl, gone: str | None) -> str | None:
        if is_deprecated(node.stability):
            gone = "itself deprecated"
        self.own.setdefault(node.target, (node, gone))
        return gone

    def _index(self, nodes: tuple[CommandDecl | GroupDecl, ...], gone: str | None) -> None:
        for node in nodes:
            within = self._add(node, gone)
            if isinstance(node, GroupDecl):
                if within == "itself deprecated":
                    within = f"deprecated with `{self._line(node)}`"
                if node.runs is not None:
                    self._add(node.runs, within)
                self._index(node.commands, within)

    def _text(self, text: str, where: str) -> None:
        """Text naming one of the plugin's own commands: pass the class, or it names none.

        The root plugin's commands share ``untaped`` with every plugin's, so
        its text is checked only when it names one of its own.
        """
        words = list(itertools.takewhile(lambda word: not word.startswith("-"), text.split()))
        if not COMMAND_TEXT.match(" ".join(words)) or not (self.root or words[1] == self.walk.name):
            return
        named = next(
            (
                self.lines[line]
                for end in range(len(words), 1, -1)
                if (line := " ".join(words[:end])) in self.lines
            ),
            None,
        )
        if named is not None:
            gone = self.own[named][1]
            detail = f"names `{text}`, " + (
                gone
                if gone is not None
                else f"a command of {self.walk.name}, as text; pass its class"
            )
        elif self.root or len(words) == 2:
            return
        else:
            detail = f"names `{text}`, which is not a command of {self.walk.name}"
        self.walk.problem("bad-replacement", f"{where}.stability", detail)

    def _line(self, node: CommandDecl | GroupDecl) -> str:
        return " ".join(("untaped", *(node.path[1:] if self.root else node.path)))

    def node[N: CommandDecl | GroupDecl](self, node: N) -> N:
        """``node`` with every replacement under it spelled."""
        where = node.target.partition(":")[2]
        update: dict[str, object] = {"stability": self.spelled(node.stability, where)}
        if isinstance(node, GroupDecl):
            update["commands"] = tuple(self.node(each) for each in node.commands)
            update["runs"] = None if node.runs is None else self.node(node.runs)
        else:
            update["options"] = tuple(
                option.model_copy(
                    update={"stability": self.spelled(option.stability, f"{where}.{option.field}")}
                )
                for option in node.options
            )
        return typing.cast("N", node.model_copy(update=update))

    def spelled(self, mark: StabilityDecl | None, where: str) -> StabilityDecl | None:
        """``mark`` with its replacement command spelled; a wrong replacement is a problem."""
        if mark is None:
            return None
        if mark.replaced_by is None:
            self._text(mark.replacement or "", where)
            return mark
        if mark.replaced_by not in self.own:
            self.walk.problem(
                "bad-replacement",
                f"{where}.stability",
                f"names {mark.replaced_by.partition(':')[2]}, which is not a command or group "
                f"of {self.walk.name}; pass one of its classes, or name another plugin's "
                "command as text (`untaped <plugin> <command>`)",
            )
            return mark
        node, gone = self.own[mark.replaced_by]
        line = self._line(node)
        if gone is not None:
            self.walk.problem("bad-replacement", f"{where}.stability", f"names `{line}`, {gone}")
        return mark.model_copy(update={"replacement": line})


class PluginWalk:
    """One plugin's walk: its declaration, the kinds it reaches, and its problems."""

    def __init__(
        self, entry: PluginEntry, owners: dict[str, str], offering: dict[type, str]
    ) -> None:
        self.name = entry.name
        self.target = entry.target
        self.root = entry.root
        module = entry.target.partition(":")[0]
        self.package = package_of(entry.target)
        self.owners = owners
        #: The plugin offering each contract class composed with this one.
        self.offering = offering
        self.problems: list[Problem] = []
        self.kinds: dict[type, str] = {}
        #: Providers' own records no command of the plugin reaches: a clash refuses
        #: nothing (see the settling in :mod:`untaped.runtime.composition`).
        self.provided: set[type] = set()
        #: The owner models of the contracts it offers.
        self.offered: set[type] = set()
        #: The models its ``records`` lists: claimed even when a provider's item too.
        self.listed: set[type] = set()
        self.decl: PluginDecl | None = None
        self.settings: type[BaseModel] | None = None
        #: The guides its commands link, by target, in the order first reached.
        self.guides: dict[str, GuideDecl] = {}
        if getattr(sys.modules.get(module), "__path__", None) is not None:
            self.problem(
                "plugin-module",
                entry.target,
                "the plugin class lives in a package __init__; move it to a leaf module "
                "(untaped_<name>.plugin) so importing the package stays light",
            )
        plugin = entry.plugin
        if not is_plugin(plugin):
            self.problem("not-a-plugin", entry.target, "names no untaped.sdk.Plugin subclass")
            return
        self.cls = plugin
        self.decl = self._plugin(plugin)

    def problem(self, rule: str, where: str, detail: str) -> None:
        self.problems.append(Problem(rule=rule, where=where, detail=detail))

    def kind(self, model: type[BaseModel]) -> str:
        """``model``'s kind: its class name under the plugin whose package defines it."""
        self.provided.discard(model)  # a command reaches it: a clash is the plugin's problem
        owner = self.owners.get(model.__module__.partition(".")[0], self.name)
        return self.kinds.setdefault(model, f"{owner}.{snake(model.__name__)}")

    def guide(
        self, cls: type, *, options: tuple[OptionDecl, ...] = (), choices: tuple[str, ...] = ()
    ) -> None:
        """Record ``cls``'s docstring as a guide, under the plugin whose package defines it."""
        target = target_of(cls)
        if target in self.guides:
            return
        doc = prose(cls)[1]
        self.guides[target] = GuideDecl(
            target=target,
            plugin=self.owners.get(cls.__module__.partition(".")[0], self.name),
            name=kebab(cls.__name__),
            summary=first_paragraph(doc),
            doc=doc,
            options=options,
            choices=choices,
        )

    def phase_of(self, cls: type) -> str:
        """When composition claims ``cls``'s kind: one of composition's phases."""
        if cls in self.offered:
            return "offered"
        if cls in self.provided and cls not in self.listed:
            return "provided"
        owner = self.owners.get(cls.__module__.partition(".")[0], self.name)
        return "own" if owner == self.name else "reached"

    def stability_of(self, cls: type, mark: object) -> StabilityDecl | None:
        """The stability ``mark`` on ``cls``; anything but a stability mark is a problem."""
        refusal = stability_refusal(mark)
        if refusal is not None:
            self.problem("reserved-name", f"{cls.__qualname__}.stability", refusal)
        return mark_stability(mark)

    # --- the plugin class ---------------------------------------------------------

    def _plugin(self, cls: type[Plugin]) -> PluginDecl | None:
        settings = self._settings(cls)
        self._reserved(cls)
        blocks = self._blocks(cls)
        #: Each attribute's value by identity: the blocks a command's fields may name.
        self.held = {id(value): attribute for attribute, value in self._attributes(cls).items()}
        #: Each state rows attribute's removers (``removes =``), by command target: where
        #: the command is, and the flag taking the key (``None``: a positional).
        self.removers: dict[str, dict[str, tuple[str, str | None]]] = {}
        commands = self._nodes(cls.commands, (self.name,), where=f"{cls.__qualname__}.commands")
        runs = None
        if cls.runs is not None and is_command(cls.runs):
            runs = self._command(cls.runs, (self.name,))
        stability = mark_stability(cls.stability)
        replacements = _Replacements(self, commands, runs, stability)
        commands = tuple(replacements.node(each) for each in commands)
        runs = None if runs is None else replacements.node(runs)
        stability = replacements.spelled(stability, cls.__qualname__)
        blocks = self._removed_by(cls, blocks)
        summary, doc = prose(cls)
        if not summary:
            self.problem(
                "missing-docstring", cls.__qualname__, "the plugin's docstring is its help line"
            )
        if settings is None or self.problems:
            return None
        skill = cls.skill.description if isinstance(cls.skill, StubSkill) else None
        return PluginDecl(
            name=self.name,
            target=self.target,
            package=self.package,
            summary=summary,
            doc=doc,
            settings=target_of(settings),
            stability=stability,
            commands=commands,
            runs=runs,
            offers=tuple(target_of(c) for c in cls.offers),
            points=self._points(cls),
            fills=self._fill_decls(cls),
            guides=tuple(self.guides.values()),
            skill=skill,
            blocks=blocks,
        )

    def _removed_by(
        self, cls: type[Plugin], blocks: tuple[BlockDecl, ...]
    ) -> tuple[BlockDecl, ...]:
        """``blocks`` with each state rows' remover recorded: one command removes rows."""
        for attribute, removers in self.removers.items():
            if len(removers) > 1:
                self.problem(
                    "duplicate-remover",
                    f"{cls.__qualname__}.{attribute}",
                    f"is removed by {', '.join(sorted(w for w, _ in removers.values()))}; "
                    "one command removes a plugin's rows",
                )
        found: list[BlockDecl] = []
        for block in blocks:
            removers = self.removers.get(block.attribute, {})
            if len(removers) == 1:
                [(target, (_, flag))] = removers.items()
                block = block.model_copy(update={"removed_by": target, "removed_with": flag})
            found.append(block)
        return tuple(found)

    def _settings(self, cls: type[Plugin]) -> type[BaseModel] | None:
        model = getattr(cls, "settings", None)
        if model is None:
            self.problem(
                "reserved-name",
                f"{cls.__qualname__}.settings",
                "every plugin declares its settings model, an empty one when it has nothing to "
                "configure: the model is its identity",
            )
            return None
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            self.problem(
                "reserved-name",
                f"{cls.__qualname__}.settings",
                f"holds {model!r}, not a pydantic model class",
            )
            return None
        self.settings = model
        return model

    def _reserved(self, cls: type[Plugin]) -> None:
        """Each reserved name holds the one thing it names."""
        where = cls.__qualname__
        held: dict[str, object] = {name: getattr(cls, name) for name in RESERVED_NAMES[1:]}
        if not isinstance(held["commands"], tuple):
            self.problem(
                "reserved-name", f"{where}.commands", "is a tuple of Command and Group classes"
            )
        self.stability_of(cls, cls.stability)
        if held["skill"] is not None and not isinstance(held["skill"], StubSkill):
            self.problem("reserved-name", f"{where}.skill", f"holds {cls.skill!r}, not a StubSkill")
        if cls.runs is not None and not is_command(cls.runs):
            self.problem(
                "reserved-name", f"{where}.runs", f"holds {cls.runs!r}, not a Command class"
            )
        self.offers = _tuple(self, cls, "offers")
        self.fills = _tuple(self, cls, "fills")
        self._records(where, _tuple(self, cls, "records"))
        if not (self.offers or self.fills):
            return
        from untaped.sdk.contracts._declare import contract_of  # noqa: PLC0415 - contracts' cost

        for offered in self.offers:
            info = contract_of(offered) if isinstance(offered, type) else None
            if info is None or info.cls is not offered:
                self.problem(
                    "reserved-name", f"{where}.offers", f"{offered!r} is not a contract class"
                )
                continue
            if (owner := self.offering.get(info.cls, self.name)) != self.name:
                self.problem(
                    "duplicate-offer",
                    f"{where}.offers",
                    f"{info.cls.__qualname__} is offered by {owner} too; a contract has one owner",
                )
            self._answer_caches(cls, info)
            if info.item is not None:
                self.kinds.setdefault(info.item, f"{self.name}.{snake(info.item.__name__)}")
                self.offered.add(info.item)
        for filled in self.fills:
            info = contract_of(filled) if isinstance(filled, type) else None
            if info is None or info.cls is filled:
                self.problem(
                    "reserved-name", f"{where}.fills", f"{filled!r} is not a provider of a contract"
                )
            elif isinstance(filled, type):
                self._provider(where, filled)

    def _records(self, where: str, listed: tuple[object, ...]) -> None:
        """The models ``records`` lists are claimed as its commands' records are, by their owner."""
        for model in listed:
            if not (isinstance(model, type) and issubclass(model, BaseModel)):
                self.problem(
                    "reserved-name", f"{where}.records", f"{model!r} is not a record model"
                )
                continue
            self.kind(model)
            self.listed.add(model)

    def _answer_caches(self, cls: type[Plugin], info: ContractInfo) -> None:
        """The name each answer cache of an offered contract takes is the plugin's to give.

        The offering plugin holds a cache ``@cached`` made as
        ``<contract>_<method>`` (:func:`held_by`); an attribute of that name
        holding anything else is refused.
        """
        for method in info.methods.values():
            cache = method.cache
            name = cache._declared_as if cache is not None else None
            if name is None or (taken := getattr(cls, name, cache)) is cache:
                continue
            self.problem(
                "answer-cache",
                f"{info.cls.__qualname__}.{method.name}",
                f"is cached as {cls.__qualname__}.{name}, which holds {taken!r}: "
                "rename that attribute",
            )

    def _provider(self, where: str, filled: type) -> None:
        """A filled class is a ``Provider``, reading its own plugin's settings if any."""
        from untaped.sdk.contracts._declare import (  # noqa: PLC0415 - contracts' cost
            ContractError,
            Provider,
            contract_of,
            item_type,
            settings_type,
        )

        at = f"{where}.fills"
        if not issubclass(filled, Provider):
            self.problem(
                "bad-provider",
                at,
                f"{filled.__qualname__} is no Provider: subclass Provider[YourSettings] beside "
                "the contract, or plain Provider when the plugin reads no settings",
            )
            return
        wanted = settings_type(filled)
        if wanted is not None and wanted is not self.settings:
            ours = self.settings.__qualname__ if self.settings else "not declared"
            self.problem(
                "bad-provider",
                at,
                f"{filled.__qualname__} reads Provider[{wanted.__qualname__}], but the plugin's "
                f"settings model is {ours}",
            )
        info = contract_of(filled)
        try:
            item = None if info is None else item_type(typing.cast("type[Contract]", filled), info)
        except ContractError:
            return  # the registry quarantines the offer, naming why
        if item is not None and info is not None and item is not info.item:
            # the provider's own record, under the plugin that fills: its rows come back through
            # the bridge
            self.kinds.setdefault(item, f"{self.name}.{snake(item.__name__)}")
            self.provided.add(item)

    def _points(self, cls: type[Plugin]) -> tuple[PointDecl, ...]:
        """The extension points ``offers`` makes, each with its prose and methods."""
        if not cls.offers:
            return ()
        from untaped.sdk.contracts._declare import contract_of  # noqa: PLC0415 - contracts' cost

        # each one is a contract class: _reserved refused the plugin otherwise
        points: list[PointDecl] = []
        for offered in cls.offers:
            info = contract_of(offered)
            if info is None:
                continue
            doc = prose(info.cls)[1]
            points.append(
                PointDecl(
                    target=target_of(info.cls),
                    name=kebab(info.cls.__name__),
                    summary=first_paragraph(doc),
                    doc=doc,
                    stability=point_stability_of(class_mark(info.cls)),
                    methods=tuple(
                        method_decl(method.name, method.function, method.hints)
                        for method in info.methods.values()
                    ),
                    types=self._types(info),
                )
            )
        return tuple(points)

    def _types(self, info: ContractInfo) -> tuple[TypeDecl, ...]:
        """The models a point's page lists: the contract's own, then each its methods name."""
        named: list[type[BaseModel]] = [] if info.item is None else [info.item]
        named += [
            model
            for method in info.methods.values()
            for hint in method.hints.values()
            for model in models_in(hint)
        ]
        return tuple(
            TypeDecl(
                name=model.__name__,
                summary=first_paragraph(prose(model)[1]),
                kind=self.kinds.get(model),
                fields=fields_of(model),
            )
            for model in dict.fromkeys(named)
        )

    def _fill_decls(self, cls: type[Plugin]) -> tuple[FillDecl, ...]:
        if not cls.fills:
            return ()
        from untaped.sdk.contracts._declare import contract_of  # noqa: PLC0415 - contracts' cost

        # each one fills a contract: _reserved refused the plugin otherwise
        return tuple(
            FillDecl(
                target=target_of(filled),
                contract=target_of(info.cls),
                summary=first_paragraph(prose(filled)[1]),
                item=item_target(filled, info),
            )
            for filled in cls.fills
            if (info := contract_of(filled)) is not None
        )

    def _attributes(self, cls: type[Plugin]) -> dict[str, object]:
        return held_by(cls)

    def _blocks(self, cls: type[Plugin]) -> tuple[BlockDecl, ...]:
        blocks: list[BlockDecl] = []
        seen: dict[int, str] = {}
        for attribute, value in self._attributes(cls).items():
            if not isinstance(value, Block):
                continue
            where = f"{cls.__qualname__}.{attribute}"
            if isinstance(value, StubSkill):
                self.problem("reserved-name", where, "a stub skill is the plugin's `skill`")
                continue
            if id(value) in seen:
                self.problem(
                    "duplicate-block", where, f"is the same {value.block_kind} as {seen[id(value)]}"
                )
                continue
            seen[id(value)] = attribute
            owners = {binding.owner for binding in value.bindings} - set(cls.__mro__)
            if owners:
                other = ", ".join(sorted(owner.__qualname__ for owner in owners))
                self.problem(
                    "duplicate-block",
                    where,
                    f"is held by {other} too; a block belongs to one plugin",
                )
            if isinstance(value, OwnedBlock) and value.spelled:
                self.problem(
                    "block-name",
                    where,
                    f"is named {value.where} by hand; a plugin class's "
                    f"{value.block_kind} takes its name from the attribute, so leave it out",
                )
                continue
            held_as = value._held_as if isinstance(value, OwnedBlock) else None
            if held_as is not None and held_as != (self.name, attribute):
                self.problem(
                    "block-name",
                    where,
                    f"is bound to {held_as[0]}.{held_as[1]} where it is declared, "
                    f"not to {self.name}.{attribute}",
                )
                continue
            name = value.derived_name(attribute)
            problem = value.name_problem(name)
            if problem is not None:
                self.problem("block-name", where, problem)
                continue
            for problem in value.problems():
                self.problem("block-shape", where, problem)
            blocks.append(
                BlockDecl(
                    block=value.block_kind,
                    attribute=attribute,
                    name=name,
                    module=value.module,
                    cache=cache_setting(value) if isinstance(value, Cache) else None,
                    layout=value.layout if isinstance(value, Dir) else None,
                    items=isinstance(value, Dir) and value.item_type is not None,
                )
            )
        return tuple(blocks)

    # --- groups and commands ------------------------------------------------------

    def _nodes(
        self, nodes: object, path: tuple[str, ...], *, where: str
    ) -> tuple[CommandDecl | GroupDecl, ...]:
        found: list[CommandDecl | GroupDecl] = []
        names: dict[str, str] = {}
        for node in nodes if isinstance(nodes, tuple) else ():
            decl = self._node(node, path, where=where)
            if decl is None:
                continue
            spellings = [decl.name]
            if isinstance(decl, CommandDecl):
                spellings += [alias.name for alias in decl.aliases]
            taken = [name for name in spellings if name in names]
            if taken:
                self.problem(
                    "duplicate-command",
                    where,
                    f"{decl.target} and {names[taken[0]]} are both `{' '.join((*path, taken[0]))}`",
                )
                continue
            names.update(dict.fromkeys(spellings, decl.target))
            found.append(decl)
        return tuple(found)

    def _node(
        self, node: object, path: tuple[str, ...], *, where: str
    ) -> CommandDecl | GroupDecl | None:
        if is_group(node):
            name = node.__name__
            if name.endswith("Group") and len(name) > len("Group"):
                name = name[: -len("Group")]
            group_path = (*path, kebab(name))
            summary, doc = prose(node)
            if not summary:
                self.problem(
                    "missing-docstring", node.__qualname__, "a group's docstring is its help line"
                )
            runs = None
            if node.runs is not None and is_command(node.runs):
                runs = self._command(node.runs, group_path)
            elif node.runs is not None:
                self.problem(
                    "reserved-name",
                    f"{node.__qualname__}.runs",
                    f"holds {node.runs!r}, not a Command class",
                )
            return GroupDecl(
                name=group_path[-1],
                path=group_path,
                target=target_of(node),
                summary=summary,
                doc=doc,
                stability=self.stability_of(node, node.stability),
                commands=self._nodes(
                    node.commands, group_path, where=f"{node.__qualname__}.commands"
                ),
                runs=runs,
            )
        if is_command(node):
            return self._command(node, (*path, kebab(node.__name__)))
        self.problem("reserved-name", where, f"holds {node!r}, not a Command or Group class")
        return None

    def _command(self, cls: type[Command[Any]], path: tuple[str, ...]) -> CommandDecl | None:
        walk = CommandWalk(self, cls, path)
        return walk.decl()


def _tuple(walk: PluginWalk, cls: type[Plugin], name: str) -> tuple[object, ...]:
    value = getattr(cls, name)
    if not isinstance(value, tuple):
        walk.problem("reserved-name", f"{cls.__qualname__}.{name}", "is a tuple of classes")
        return ()
    return value


def is_group(obj: object) -> typing.TypeGuard[type[Group]]:
    return isinstance(obj, type) and issubclass(obj, Group)


def is_plugin(obj: object) -> typing.TypeGuard[type[Plugin]]:
    return isinstance(obj, type) and issubclass(obj, Plugin)


def is_command(obj: object) -> typing.TypeGuard[type[Command[Any]]]:
    return isinstance(obj, type) and issubclass(obj, Command)


def item_target(provider: type, info: ContractInfo) -> str | None:
    """The provider's own record (its ``T`` when not the contract's), which a bridge reads."""
    from untaped.sdk.contracts._declare import (  # noqa: PLC0415 - contracts' cost
        ContractError,
        item_type,
    )

    try:
        item = item_type(typing.cast("type[Contract]", provider), info)
    except ContractError:
        return None  # the registry quarantines the offer, naming why
    return None if item is None or item is info.item else target_of(item)
