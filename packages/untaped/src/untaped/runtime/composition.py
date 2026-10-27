"""Composition: one walk from the plugin classes to the :class:`Declaration`, with its checks.

Core starts from each plugin class an entry point names and follows its
references. Everything it reaches is the plugin's, named after the
attribute or class under the plugin's name: commands from class names in
kebab case, kinds from the classes ``Output[R]`` and ``File[M]`` name, blocks
from their attributes. What the shape cannot prevent is checked here, once,
and each problem names the class or field it is about; a plugin with any
problem is quarantined and the others compose.
"""

from __future__ import annotations

import collections.abc
import enum
import inspect
import re
import sys
import types
import typing
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import TYPE_CHECKING, Any, get_args, get_origin, get_type_hints

from pydantic import BaseModel
from pydantic_core import PydanticUndefined, to_jsonable_python

from untaped.runtime.declaration import (
    AliasDecl,
    BlockDecl,
    CacheDecl,
    CommandDecl,
    Declaration,
    FillDecl,
    GroupDecl,
    KindDecl,
    OptionDecl,
    PluginDecl,
    Problem,
    Quarantined,
    Returns,
    StabilityDecl,
    target_of,
)
from untaped.runtime.root_options import ROOT_FLAGS
from untaped.sdk._block import OR_STDIN, POSITIONAL, Block, FieldBlock, OwnedBlock
from untaped.sdk.fields import (
    CORE_SHORTS,
    AllowHyphen,
    File,
    Output,
    Parallel,
    Refresh,
    SettingKey,
    Short,
    Stdin,
    Written,
    unprintable_following,
)
from untaped.sdk.marks import Deprecated, Experimental
from untaped.sdk.plugin import (
    RESERVED_NAMES,
    Command,
    Group,
    Plugin,
    StubSkill,
    field_block,
    generic_argument,
    held_by,
    settings_model,
)
from untaped.sdk.storage.cache import Cache, _max_age_setting, _refresh_help
from untaped.sdk.storage.dir import Dir

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from pydantic.fields import FieldInfo

    from untaped.sdk.contracts._declare import Contract, ContractInfo

#: Field names core or pydantic reserves on a command; ``run_`` still spells ``--run``.
RESERVED_FIELDS: frozenset[str] = frozenset(
    {
        "run",
        "ctx",
        "settings",
        "spelled",
        "stability",
        "aliases",
        "repairs",
        "json",
        "copy",
        "schema",
        "validate",
        "dict",
        "construct",
    }
)

_WORDS = re.compile(r"[A-Z]+(?=[A-Z][a-z0-9])|[A-Z]?[a-z0-9]+|[A-Z]+")
_ITERATORS = frozenset(
    {
        collections.abc.Iterator,
        collections.abc.Iterable,
        collections.abc.Generator,
        typing.Iterator,
        typing.Iterable,
        typing.Generator,
    }
)


def words(name: str) -> list[str]:
    """The words of a class name, a run of capitals one word: ``SSHKeys`` is ``ssh``, ``keys``."""
    return [word.lower() for word in _WORDS.findall(name)]


def bundle_panel(model: type) -> str:
    """A bundle's help panel: its class name as words, ``SourceData`` is ``Source data``."""
    first, *rest = _WORDS.findall(model.__name__)
    return " ".join([first, *(word if word.isupper() else word.lower() for word in rest)])


def kebab(name: str) -> str:
    """A class name as a command word: ``JobTemplates`` is ``job-templates``."""
    return "-".join(words(name))


def snake(name: str) -> str:
    """A class name as a kind's last part: ``TestResult`` is ``test_result``."""
    return "_".join(words(name))


@dataclass(frozen=True)
class PluginEntry:
    """One entry point: the plugin's name (its key), its target, and the object it loads.

    ``root`` is core's own entry, which has no entry point: its commands mount
    at the root, so their words are taken as plugin names are.
    """

    name: str
    target: str
    plugin: object
    root: bool = False


def compose(entries: Sequence[PluginEntry], *, taken: Iterable[str] = ()) -> Declaration:
    """Walk ``entries`` in order into one :class:`Declaration`.

    ``taken`` are names a plugin may not have: config.yml's own top-level
    keys and the root commands not on a plugin class. A plugin whose name is
    taken, by those, by an earlier entry sound on its own or by such a root
    entry's commands (core composes first and wins), or whose walk finds any
    problem, is quarantined with every problem named. A record a command
    reaches belongs to the plugin whose package defines it, whichever plugins
    reach it and in whatever order; a class no composed plugin's package
    defines belongs to the plugin that reaches it. A contract's records
    belong to the plugin that lists them: the owner's model to the plugin
    that ``offers`` the contract (shelf's ``Book`` is ``shelf.book``), a
    provider's own record to the plugin whose ``fills`` lists the provider.
    A record has one kind, and a kind one record: claims rank by
    :data:`_PHASES` across every plugin, a plugin's own records before
    another's reach of its package, and a plugin is refused
    (``duplicate-kind``) when its claim clashes with a composing plugin's
    better one, or with the plugin a cycle of plugins outranking each other
    composes (the first of it in entry order); entry order decides nothing
    else but ranks within a phase. A plugin refused claims no kind; a name
    goes to the first plugin sound on its own, before kinds are settled.
    Two entry points in one top-level package share it, the first not
    quarantined owning its classes, as :meth:`Declaration.owner_of` reads
    it. A contract belongs to the one plugin whose ``offers`` lists it; two
    plugins listing it are both refused (``duplicate-offer``), as a fill or refresh of a contract no
    composed plugin owns is (``unowned-cache``). Composing is a fixpoint: a
    plugin broken whatever the others are (a name ``taken`` holds included)
    is withdrawn first, then the plugins listing one contract; the others
    then read owners (of a package, a contract) only among the plugins that
    compose, until those they read are those that compose (when no such set
    exists, the first plugin refused as soon as it is read is, first-wins).
    """
    reserved = frozenset(taken)
    every = frozenset(range(len(entries)))
    # Refused for good, by index, as it was refused: there are at most n withdrawals.
    withdrawn: dict[int, tuple[Problem, ...]] = {}
    read: frozenset[int] | None = None  # the plugins the others read; None: all standing
    tried: set[frozenset[int]] = set()
    # each plugin's problems the last time it was refused in this phase
    last: dict[int, tuple[Problem, ...]] = {}

    def attempt(
        standing: frozenset[int], readable: frozenset[int]
    ) -> tuple[Declaration, dict[int, tuple[Problem, ...]]]:
        """One pass: the others read owners only among ``readable``."""
        owners: dict[str, str] = {}
        for i in sorted(readable):
            owners.setdefault(_package(entries[i].target), entries[i].name)
        offering = _offering(entries, sorted(standing))
        declaration, refused = _compose(entries, reserved, owners, offering, withdrawn, readable)
        return declaration, {i: problems for i, problems in refused.items() if i not in withdrawn}

    while True:
        standing = every - frozenset(withdrawn)
        readable = standing if read is None else read & standing
        declaration, refused = attempt(standing, readable)
        last |= refused
        final = {
            i: problems
            for i, problems in refused.items()
            if _broken_alone(problems) or entries[i].name in reserved  # taken for good
        } or _with_rule(refused, "duplicate-offer")
        if final:
            withdrawn |= final
            read, tried, last = None, set(), {}
            continue
        composed = standing - frozenset(refused)
        if composed == readable:
            return declaration
        if composed not in tried:
            read = composed
        elif composed & readable < readable:
            read = composed & readable  # a set seen before: only shrink, so this ends
        else:
            # a plugin composes only while it isn't read (reading it changes the
            # kinds or contract owners the others see): no read set
            # agrees, so the first plugin refused once read beside those read
            # is refused as it was when read, first-wins, and the rest compose
            # without it (one composing only while unread for another's sake waits)
            unread = sorted(composed - readable)
            first = next(
                (c for c in unread if c in attempt(standing, readable | {c})[1]), unread[0]
            )
            withdrawn[first] = last[first]
            read, tried, last = None, set(), {}
            continue
        tried.add(composed)


def _with_rule(
    refused: dict[int, tuple[Problem, ...]], rule: str
) -> dict[int, tuple[Problem, ...]]:
    """The refused entries with a ``rule`` problem."""
    return {i: problems for i, problems in refused.items() if any(p.rule == rule for p in problems)}


#: Problems a plugin has because of the others composed beside it, in this pass.
_RELATIONAL = frozenset({"duplicate-name", "duplicate-kind", "duplicate-offer", "unowned-cache"})


def _broken_alone(problems: Sequence[Problem]) -> bool:
    """Whether a plugin has a problem that no other plugin causes."""
    return any(problem.rule not in _RELATIONAL for problem in problems)


def _offered(entry: PluginEntry) -> tuple[type, ...]:
    """The contract classes ``entry``'s plugin class lists in ``offers``."""
    offers = getattr(entry.plugin, "offers", ()) if _is_plugin(entry.plugin) else ()
    return (
        tuple(each for each in offers if isinstance(each, type))
        if isinstance(offers, tuple)
        else ()
    )


def _offering(
    entries: Sequence[PluginEntry], standing: Iterable[int]
) -> dict[type, tuple[int, ...]]:
    """The standing entries listing each contract class, by index: its owner, when it is one."""
    found: dict[type, list[int]] = {}
    for index in standing:
        for each in dict.fromkeys(_offered(entries[index])):
            found.setdefault(each, []).append(index)
    return {contract: tuple(listers) for contract, listers in found.items()}


def _view(
    entries: Sequence[PluginEntry],
    index: int,
    offering: dict[type, tuple[int, ...]],
    readable: frozenset[int],
) -> dict[type, str]:
    """The owner of each contract as entry ``index`` reads it, its own offers as when alone.

    A contract another entry lists too reads as that one's (``duplicate-offer``);
    one only ``index`` lists is its own. A contract owns nobody else's reads
    when two list it, or when its one lister lists such a contract: that
    lister is refused, so a fill or refresh of it is ``unowned-cache``; so
    is one whose lister is not ``readable`` (it does not compose).
    """
    contested = {each for listers in offering.values() if len(listers) > 1 for each in listers}
    view: dict[type, str] = {}
    for contract, listers in offering.items():
        if index in listers:
            name = entries[index].name
            others = [
                entries[each].name if entries[each].name != name else f"another {name}"
                for each in listers
                if each != index
            ]
            view[contract] = " and ".join(others) if others else name
        elif len(listers) == 1 and listers[0] not in contested and listers[0] in readable:
            view[contract] = entries[listers[0]].name
    return view


def _cache_setting(cache: Cache[Any]) -> CacheDecl:
    max_age = cache.default_max_age
    return CacheDecl(
        label=cache.label,
        max_age_seconds=None if max_age is None else int(max_age.total_seconds()),
        renamed_from=cache.renamed_from,
        removed_in=cache.removed_in,
    )


def _compose(
    entries: Sequence[PluginEntry],
    taken: frozenset[str],
    owners: dict[str, str],
    offering: dict[type, tuple[int, ...]],
    withdrawn: dict[int, tuple[Problem, ...]],
    readable: frozenset[int],
) -> tuple[Declaration, dict[int, tuple[Problem, ...]]]:
    """One pass over ``entries``: the declaration and each refused entry's problems, by index.

    A ``withdrawn`` entry is quarantined with the problems it was withdrawn for, unwalked.
    """
    plugins: list[PluginDecl] = []
    quarantined: list[Quarantined] = []
    refused: dict[int, tuple[Problem, ...]] = {}
    walks: dict[int, tuple[PluginEntry, _PluginWalk]] = {}
    for index, entry in enumerate(entries):
        if index not in withdrawn:
            walks[index] = (
                entry,
                _PluginWalk(entry, owners, _view(entries, index, offering, readable)),
            )
    for _, walk in walks.values():
        walk.problems.extend(_own_clashes(walk))
    sound = [
        index for index, (_, walk) in walks.items() if not walk.problems and walk.decl is not None
    ]
    # Names first, in entry order among the plugins sound on their own, then kinds
    # among those holding their name.
    before, named = _names(walks, sound, taken)
    held, clashes = _settle({index: walks[index][1] for index in named})
    kinds: dict[str, KindDecl] = {}
    for index, entry in enumerate(entries):
        if index in withdrawn:
            quarantined.append(
                Quarantined(name=entry.name, target=entry.target, problems=withdrawn[index])
            )
            continue
        _, walk = walks[index]
        problems = [*walk.problems, *clashes.get(index, ())]
        if entry.name in before[index]:
            problems.insert(
                0,
                Problem(
                    rule="duplicate-name",
                    where=entry.name,
                    detail="the name is taken by a root command, a config key or another plugin",
                ),
            )
        if problems or walk.decl is None:
            quarantined.append(
                Quarantined(name=entry.name, target=entry.target, problems=tuple(problems))
            )
            refused[index] = tuple(problems)
            continue
        if entry.root:
            walk.decl = walk.decl.model_copy(update={"root": True})
        plugins.append(walk.decl)
        kinds.update(held.get(index, {}))
    declaration = Declaration(
        plugins=tuple(plugins),
        kinds=tuple(sorted(kinds.values(), key=lambda kind: kind.kind)),
        quarantined=tuple(quarantined),
    )
    return declaration, refused


def _package(target: str) -> str:
    """The top-level package of an entry point's module: every class under it is the plugin's."""
    return target.partition(":")[0].partition(".")[0]


def _names(
    walks: dict[int, tuple[PluginEntry, _PluginWalk]], sound: list[int], taken: frozenset[str]
) -> tuple[dict[int, frozenset[str]], list[int]]:
    """The names taken before each entry, and the ``sound`` entries holding their name."""
    names = set(taken)
    before: dict[int, frozenset[str]] = {}
    named: list[int] = []
    for index, (entry, walk) in walks.items():
        before[index] = frozenset(names)
        if index in sound and entry.name not in names:
            named.append(index)
            names.add(entry.name)
            if entry.root and walk.decl is not None:
                names.update(walk.decl.command_words())
    return before, named


#: How a claim of a kind ranks, best first, across every plugin, so no entry order
#: decides between them: the models offered contracts own, the records a command
#: reaches from its plugin's own package (or one no composed plugin owns), those it
#: reaches from another plugin's package; the records only a provider reaches are
#: claimed last and refuse nothing.
_PHASES = ("offered", "own", "reached", "provided")


@dataclass(frozen=True)
class _Claim:
    """A plugin's claim of ``kind`` for the class ``target``, ranked by phase, then entry."""

    rank: tuple[int, int]
    kind: str
    target: str
    where: str


def _clash(claim: _Claim, other: _Claim) -> str | None:
    """Why ``claim`` cannot stand beside ``other``: two classes one kind, or one class two."""
    if claim.kind == other.kind and claim.target != other.target:
        return f"would be the kind {claim.kind}, which {other.target} is: rename one"
    if claim.target == other.target and claim.kind != other.kind:
        return f"would be the kind {claim.kind}, but it is {other.kind}: a record has one kind"
    return None


def _claims(index: int, walk: _PluginWalk) -> list[_Claim]:
    """The kinds entry ``index`` claims for the records its commands and offers reach."""
    return [
        _Claim((_PHASES.index(phase), index), kind, target_of(cls), cls.__qualname__)
        for cls, kind in walk.kinds.items()
        if (phase := walk.phase_of(cls)) != "provided"
    ]


def _own_clashes(walk: _PluginWalk) -> list[Problem]:
    """Two of ``walk``'s records of one kind: the plugin is broken on its own."""
    problems: list[Problem] = []
    first: dict[str, _Claim] = {}
    for claim in _claims(0, walk):
        why = _clash(claim, first.setdefault(claim.kind, claim))
        if why is not None:
            problems.append(Problem(rule="duplicate-kind", where=claim.where, detail=why))
    return problems


def _settle(
    walks: dict[int, _PluginWalk],
) -> tuple[dict[int, dict[str, KindDecl]], dict[int, list[Problem]]]:
    """The kinds each of ``walks`` holds, and the kind clashes of those refused, by index.

    Each plugin's own claims agree (see :func:`_own_clashes`). When two
    plugins' claims clash, the better-ranked claim's plugin (:data:`_PHASES`,
    then entry order) outranks the other; each may outrank the other through
    two clashes. A plugin composes when no composing plugin outranks it, so a
    refused plugin decides nothing; when plugins outrank each other in a
    cycle no other plugin outranks, the first of it in entry order composes.
    A record only a provider reaches refuses nothing: it keeps the kind
    another plugin gave the class, or, two classes one kind, the later has none.
    """
    claims = {index: _claims(index, walk) for index, walk in walks.items()}
    against, outranked = _rivals(claims)
    clashes: dict[int, list[Problem]] = {}
    remaining = list(claims)
    composing: list[int] = []
    while remaining:
        pick = _unbeaten(remaining, outranked)
        composing.append(pick)
        remaining.remove(pick)
        for index in [each for each in remaining if pick in against[each]]:
            clashes[index] = against[index][pick]
            remaining.remove(index)
    kinds = {claim.kind: claim for index in composing for claim in claims[index]}
    held: dict[int, list[_Claim]] = {index: list(claims[index]) for index in sorted(composing)}
    for index, holding in held.items():
        walk = walks[index]
        for cls in [each for each in walk.kinds if each in walk.provided]:
            claim = _Claim((len(_PHASES), index), walk.kinds[cls], target_of(cls), "")
            if not any(_clash(claim, other) for other in kinds.values()):
                kinds[claim.kind] = claim
                holding.append(claim)
    return {
        index: {
            claim.kind: KindDecl(
                kind=claim.kind, target=claim.target, plugin=claim.kind.partition(".")[0]
            )
            for claim in holding
        }
        for index, holding in held.items()
    }, clashes


def _unbeaten(remaining: list[int], outranked: dict[int, set[int]]) -> int:
    """The first of ``remaining`` that nothing remaining outranks, or else, when each is,
    the first whose every remaining rival above it is also below it (a cycle no other
    plugin outranks)."""
    left = set(remaining)

    def above(index: int) -> set[int]:
        found: set[int] = set()
        todo = [index]
        while todo:
            for each in outranked[todo.pop()] & left:
                if each not in found:
                    found.add(each)
                    todo.append(each)
        return found

    free = [index for index in remaining if not outranked[index] & left]
    if free:
        return free[0]
    return next(index for index in remaining if all(index in above(each) for each in above(index)))


def _rivals(
    claims: dict[int, list[_Claim]],
) -> tuple[dict[int, dict[int, list[Problem]]], dict[int, set[int]]]:
    """Each plugin's claims clashing with another's, by both; and who outranks each plugin."""
    against: dict[int, dict[int, list[Problem]]] = {index: {} for index in claims}
    outranked: dict[int, set[int]] = {index: set() for index in claims}
    by_kind: dict[str, list[tuple[int, _Claim]]] = {}
    by_target: dict[str, list[tuple[int, _Claim]]] = {}
    for index, ours in claims.items():
        for claim in ours:
            by_kind.setdefault(claim.kind, []).append((index, claim))
            by_target.setdefault(claim.target, []).append((index, claim))
    for b, theirs in claims.items():
        for claim in theirs:
            for a, other in [*by_kind[claim.kind], *by_target[claim.target]]:
                if a != b and (why := _clash(claim, other)) is not None:
                    problem = Problem(rule="duplicate-kind", where=claim.where, detail=why)
                    against[b].setdefault(a, []).append(problem)
                    if other.rank < claim.rank:
                        outranked[b].add(a)
    return against, outranked


def _stability(mark: object) -> StabilityDecl | None:
    if isinstance(mark, Experimental):
        return StabilityDecl(stability="experimental")
    if isinstance(mark, Deprecated):
        replacement = mark.replacement
        text = None
        if isinstance(replacement, str | SettingKey):
            text = str(replacement)
        elif replacement is not None:
            text = target_of(replacement)
        return StabilityDecl(stability="deprecated", removed_in=mark.removed_in, replacement=text)
    return None


def _prose(cls: type) -> tuple[str, str]:
    """The summary (first line) and the whole docstring of ``cls`` (its own, never inherited)."""
    doc = inspect.cleandoc(cls.__dict__.get("__doc__") or "")
    return doc.partition("\n")[0].strip(), doc


class _PluginWalk:
    """One plugin's walk: its declaration, the kinds it reaches, and its problems."""

    def __init__(
        self, entry: PluginEntry, owners: dict[str, str], offering: dict[type, str]
    ) -> None:
        self.name = entry.name
        self.target = entry.target
        module = entry.target.partition(":")[0]
        self.package = _package(entry.target)
        self.owners = owners
        #: The plugin offering each contract class composed with this one.
        self.offering = offering
        self.problems: list[Problem] = []
        self.kinds: dict[type, str] = {}
        #: Providers' own records no command of the plugin reaches: a clash refuses
        #: nothing (see :func:`_settle`).
        self.provided: set[type] = set()
        #: The owner models of the contracts it offers.
        self.offered: set[type] = set()
        self.decl: PluginDecl | None = None
        self.settings: type[BaseModel] | None = None
        if getattr(sys.modules.get(module), "__path__", None) is not None:
            self.problem(
                "plugin-module",
                entry.target,
                "the plugin class lives in a package __init__; move it to a leaf module "
                "(untaped_<name>.plugin) so importing the package stays light",
            )
        plugin = entry.plugin
        if not _is_plugin(plugin):
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

    def phase_of(self, cls: type) -> str:
        """When composition claims ``cls``'s kind, one of :data:`_PHASES`."""
        if cls in self.offered:
            return "offered"
        if cls in self.provided:
            return "provided"
        owner = self.owners.get(cls.__module__.partition(".")[0], self.name)
        return "own" if owner == self.name else "reached"

    def stability_of(self, cls: type, mark: object) -> StabilityDecl | None:
        """The stability ``mark`` on ``cls``; anything but a stability mark is a problem."""
        decl = _stability(mark)
        if mark is not None and decl is None:
            self.problem(
                "reserved-name",
                f"{cls.__qualname__}.stability",
                f"holds {mark!r}, not experimental or deprecated(...)",
            )
        return decl

    # --- the plugin class ---------------------------------------------------------

    def _plugin(self, cls: type[Plugin]) -> PluginDecl | None:
        settings = self._settings(cls)
        self._reserved(cls)
        blocks = self._blocks(cls)
        #: Each attribute's value by identity: the blocks a command's fields may name.
        self.held = {id(value): attribute for attribute, value in self._attributes(cls).items()}
        #: Each state rows attribute's removers (``removes =``), by command target.
        self.removers: dict[str, dict[str, str]] = {}
        commands = self._nodes(cls.commands, (self.name,), where=f"{cls.__qualname__}.commands")
        runs = None
        if cls.runs is not None and _is_command(cls.runs):
            runs = self._command(cls.runs, (self.name,))
        blocks = self._removed_by(cls, blocks)
        summary, doc = _prose(cls)
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
            stability=_stability(cls.stability),
            commands=commands,
            runs=runs,
            offers=tuple(target_of(c) for c in cls.offers),
            fills=self._fill_decls(cls),
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
                    f"is removed by {', '.join(sorted(removers.values()))}; "
                    "one command removes a plugin's rows",
                )
        return tuple(
            block.model_copy(update={"removed_by": next(iter(removers))})
            if len(removers := self.removers.get(block.attribute, {})) == 1
            else block
            for block in blocks
        )

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
        if cls.runs is not None and not _is_command(cls.runs):
            self.problem(
                "reserved-name", f"{where}.runs", f"holds {cls.runs!r}, not a Command class"
            )
        self.offers = _tuple(self, cls, "offers")
        self.fills = _tuple(self, cls, "fills")
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

    def _answer_caches(self, cls: type[Plugin], info: ContractInfo) -> None:
        """The name each answer cache of an offered contract takes is the plugin's to give.

        The offering plugin holds a cache ``@cached`` made as
        ``<contract>_<method>`` (:func:`held_by`); an attribute of that name
        holding anything else is refused.
        """
        for method in info.methods.values():
            cache = method.cache
            name = cache._declared_as if cache is not None and not cache.spelled else None
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

    def _fill_decls(self, cls: type[Plugin]) -> tuple[FillDecl, ...]:
        if not cls.fills:
            return ()
        from untaped.sdk.contracts._declare import contract_of  # noqa: PLC0415 - contracts' cost

        # each one fills a contract: _reserved refused the plugin otherwise
        return tuple(
            FillDecl(target=target_of(filled), contract=target_of(info.cls))
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
                    cache=_cache_setting(value) if isinstance(value, Cache) else None,
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
        if _is_group(node):
            name = node.__name__
            if name.endswith("Group") and len(name) > len("Group"):
                name = name[: -len("Group")]
            group_path = (*path, kebab(name))
            summary, doc = _prose(node)
            if not summary:
                self.problem(
                    "missing-docstring", node.__qualname__, "a group's docstring is its help line"
                )
            runs = None
            if node.runs is not None and _is_command(node.runs):
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
        if _is_command(node):
            return self._command(node, (*path, kebab(node.__name__)))
        self.problem("reserved-name", where, f"holds {node!r}, not a Command or Group class")
        return None

    def _command(self, cls: type[Command[Any]], path: tuple[str, ...]) -> CommandDecl | None:
        walk = _CommandWalk(self, cls, path)
        return walk.decl()


def _tuple(walk: _PluginWalk, cls: type[Plugin], name: str) -> tuple[object, ...]:
    value = getattr(cls, name)
    if not isinstance(value, tuple):
        walk.problem("reserved-name", f"{cls.__qualname__}.{name}", "is a tuple of classes")
        return ()
    return value


def _is_group(obj: object) -> typing.TypeGuard[type[Group]]:
    return isinstance(obj, type) and issubclass(obj, Group)


def _is_plugin(obj: object) -> typing.TypeGuard[type[Plugin]]:
    return isinstance(obj, type) and issubclass(obj, Plugin)


def _is_command(obj: object) -> typing.TypeGuard[type[Command[Any]]]:
    return isinstance(obj, type) and issubclass(obj, Command)


def _is_model(obj: object) -> typing.TypeGuard[type[BaseModel]]:
    return isinstance(obj, type) and issubclass(obj, BaseModel)


@dataclass
class _Seen:
    """What a command's fields add up to while they are walked."""

    options: list[OptionDecl] = field(default_factory=list)
    blocks: Counter[str] = field(default_factory=Counter)
    guides: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    stdin: list[str] = field(default_factory=list)
    stdin_via: str | None = None
    output: type[BaseModel] | None = None
    formats: tuple[str, ...] = ()


class _CommandWalk:
    """One command's walk: its options, what it reads and prints, and its problems."""

    def __init__(self, plugin: _PluginWalk, cls: type[Command[Any]], path: tuple[str, ...]) -> None:
        self.plugin = plugin
        self.cls = cls
        self.path = path
        self.where = cls.__qualname__
        self.seen = _Seen()
        #: How many optional bundles the walk is inside: their fields are never required.
        self.optional_bundles = 0

    def problem(self, rule: str, where: str, detail: str) -> None:
        self.plugin.problem(rule, where, detail)

    def decl(self) -> CommandDecl | None:
        cls = self.cls
        if not cls.__pydantic_complete__:
            try:
                cls.model_rebuild(raise_errors=True)
            except Exception as exc:
                self.problem(
                    "unresolved-type", self.where, f"a field type is not defined at run time: {exc}"
                )
                return None
        self._settings()
        returns = self._returns()
        for name, info in cls.model_fields.items():
            self._field(name, info, prefix="", panel="Options")
        self._duplicates()
        self._positionals()
        self._output(returns)
        self._removes()
        summary, doc = _prose(cls)
        if not summary:
            self.problem("missing-docstring", self.where, "a command's docstring is its help")
        if returns is None:
            return None
        seen = self.seen
        return CommandDecl(
            name=self.path[-1],
            path=self.path,
            target=target_of(cls),
            summary=summary,
            doc=doc,
            settings=self.plugin.name if settings_model(cls) is not None else None,
            options=tuple(seen.options),
            returns=returns[0],
            output=None if seen.output is None else self._kind(seen.output),
            formats=seen.formats,
            stdin=tuple(seen.stdin),
            stdin_via=seen.stdin_via,
            inputs=tuple(seen.inputs),
            writes=tuple(seen.writes),
            guides=tuple(dict.fromkeys(seen.guides)),
            asks_first=seen.blocks["confirm"] > 0,
            stability=self._mark(cls),
            aliases=self._aliases(),
            repairs=self._repairs(),
        )

    def _removes(self) -> None:
        """Record the state rows the command marks ``removes =``: rows its plugin class holds."""
        rows = self.cls.removes
        if rows is None:
            return
        attribute = self.plugin.held.get(id(rows))
        if attribute is None or getattr(rows, "block_kind", None) != "state-rows":
            self.problem(
                "reserved-name",
                f"{self.where}.removes",
                f"holds {rows!r}, not state rows its plugin class holds",
            )
            return
        self.plugin.removers.setdefault(attribute, {})[target_of(self.cls)] = self.where

    def _mark(self, cls: type[Command[Any]]) -> StabilityDecl | None:
        return self.plugin.stability_of(cls, cls.stability)

    def _repairs(self) -> bool:
        repairs: object = self.cls.repairs
        if not isinstance(repairs, bool):
            self.problem("reserved-name", f"{self.where}.repairs", f"holds {repairs!r}, not a bool")
            return False
        return repairs

    def _aliases(self) -> tuple[AliasDecl, ...]:
        aliases = self.cls.aliases
        if not isinstance(aliases, tuple) or not all(
            isinstance(alias, Deprecated) and isinstance(alias.replacement, str)
            for alias in aliases
        ):
            self.problem(
                "reserved-name",
                f"{self.where}.aliases",
                f"holds {aliases!r}, not a tuple of "
                'Deprecated(replacement="old-name", removed_in=...)',
            )
            return ()
        return tuple(
            AliasDecl(name=str(alias.replacement), removed_in=alias.removed_in) for alias in aliases
        )

    def _kind(self, model: type[BaseModel]) -> str:
        return self.plugin.kind(model)

    def _settings(self) -> None:
        model = settings_model(self.cls)
        if model is not None and model is not getattr(self.plugin, "settings", None):
            self.problem(
                "settings-model",
                self.where,
                f"is Command[{model.__name__}], but the plugin's settings model is "
                f"{getattr(self.plugin, 'settings', type(None)).__name__}",
            )

    # --- what run returns ----------------------------------------------------------

    def _returns(self) -> tuple[Returns, type[BaseModel] | None] | None:
        run = self.cls.run
        if run is Command.run:
            self.problem("missing-run", self.where, "defines no run()")
            return None
        try:
            hints = get_type_hints(run)
        except Exception as exc:
            self.problem(
                "unresolved-type",
                f"{self.where}.run",
                f"the return type is not defined at run time: {exc}",
            )
            return None
        if "return" not in hints:
            self.problem(
                "missing-return",
                f"{self.where}.run",
                "needs a return annotation: it says what the command prints",
            )
            return None
        shape = _shape(hints["return"])
        if shape is None:
            self.problem(
                "output-return",
                f"{self.where}.run",
                f"returns {hints['return']!r}; core prints Iterator[R], R, R | None, "
                "R | Iterator[R], str, str | None or None",
            )
        return shape

    def _output(self, returns: tuple[Returns, type[BaseModel] | None] | None) -> None:
        if returns is None:
            return
        shape, model = returns
        output = self.seen.output
        if model is not None and output is None:
            self.problem(
                "output-return",
                self.where,
                f"run returns {model.__name__} records but declares no Output[R] field",
            )
        elif output is not None and model is None:
            self.problem(
                "output-return",
                self.where,
                f"declares Output[{output.__name__}] but run returns {shape}, not records",
            )
        elif output is not None and model is not None and not issubclass(output, model):
            self.problem(
                "output-return",
                self.where,
                f"declares Output[{output.__name__}] but run returns {model.__name__}, "
                "which is not it or a base of it",
            )
        for name in unprintable_following(output) if output is not None else ():
            self.problem(
                "field-type",
                f"{self.where}.output",
                f"{target_of(output)}.{name} is marked Following but is not a list of models: "
                "a table prints a Following field as its own table of the items",
            )

    # --- fields ------------------------------------------------------------------

    def _field(self, name: str, info: FieldInfo, *, prefix: str, panel: str) -> None:
        path = f"{prefix}{name}"
        where = f"{self.where}.{path}"
        if name in RESERVED_FIELDS:
            self.problem(
                "reserved-field",
                where,
                f"core or pydantic reserves `{name}`; name the field `{name}_` and it "
                f"still spells --{name}",
            )
            return
        annotation = info.annotation
        block = field_block(annotation)
        if block is not None:
            self._core(path, where, block, info)
            return
        if _nested_block(annotation):
            self.problem(
                "field-type",
                where,
                "a core-filled field type is the whole field type, never inside another",
            )
            return
        value, repeated, inner = _unwrap(annotation)
        marks = (*info.metadata, *inner)
        if _is_model(value) and POSITIONAL not in marks and not repeated:
            self._bundle(path, value, required=info.is_required())
            return
        self._option(path, where, info, value=value, marks=marks, repeated=repeated, panel=panel)

    def _bundle(self, path: str, model: type[BaseModel], *, required: bool) -> None:
        if _prose(model)[0]:
            self.seen.guides.append(target_of(model))
        self.optional_bundles += not required
        for name, info in model.model_fields.items():
            self._field(name, info, prefix=f"{path}.", panel=bundle_panel(model))
        self.optional_bundles -= not required

    def _option(
        self,
        path: str,
        where: str,
        info: FieldInfo,
        *,
        value: object,
        marks: tuple[object, ...],
        repeated: bool,
        panel: str,
    ) -> None:
        token = self._token(value, where)
        if token is None:
            return
        name = path.rpartition(".")[2].rstrip("_")
        positional = POSITIONAL in marks
        if not info.description:
            self.problem(
                "missing-description",
                where,
                "every option and argument has help: Field(description=...)",
            )
        shorts = [mark.flag for mark in marks if isinstance(mark, Short)]
        for short in shorts:
            if short in CORE_SHORTS:
                self.problem("core-short", where, f"{short} belongs to core's own flags")
        default = None if info.default is PydanticUndefined else info.default
        negative = None
        if token[0] == "bool" and not positional and default is True:
            negative = f"--no-{name.replace('_', '-')}"
        flag = None if positional else f"--{name.replace('_', '-')}"
        for spelled in (flag, negative, *shorts):
            if spelled in ROOT_FLAGS:
                fix = "give it another Short" if spelled in shorts else "rename the field"
                self.problem(
                    "root-option",
                    where,
                    f"{spelled} is a root option, which every command takes "
                    f"(`untaped {spelled}`); {fix}",
                )
        self.seen.options.append(
            OptionDecl(
                field=path,
                flag=flag,
                metavar=name.upper(),
                positional=positional,
                token=f"list[{token[0]}]" if repeated else token[0],
                choices=token[1],
                negative=negative,
                short=shorts[0] if shorts else None,
                required=info.is_required() and not self.optional_bundles,
                default=_jsonable(default),
                help=info.description or "",
                panel=panel,
                allow_hyphen=AllowHyphen in marks,
                or_stdin=OR_STDIN in marks,
                reads_file=get_origin(value) is File and Written not in marks,
                stability=next((s for s in map(_stability, marks) if s is not None), None),
            )
        )
        self._file(value, marks)

    def _token(self, value: object, where: str) -> tuple[str, tuple[str, ...]] | None:
        token = _token(value)
        if token is None:
            self.problem(
                "field-type",
                where,
                f"core makes no option of {getattr(value, '__name__', value)!r}: use a plain type, "
                "a Literal, an enum, a pydantic model (a bundle) or one of core's value types",
            )
            return None
        if (
            isinstance(value, type)
            and issubclass(value, enum.Enum)
            and _prose(value)[0]
            and not value.__module__.startswith(("untaped.", "enum"))
        ):
            self.seen.guides.append(target_of(value))
        return token

    def _file(self, value: object, marks: tuple[object, ...]) -> None:
        if get_origin(value) is not File:
            return
        model = generic_argument(value)
        if not _is_model(model):
            self.problem("field-type", self.where, "File[M] takes a pydantic model M")
            return
        (self.seen.writes if Written in marks else self.seen.inputs).append(self._kind(model))

    def _core(self, path: str, where: str, block: type[FieldBlock], info: FieldInfo) -> None:
        seen = self.seen
        if seen.blocks[block.field_kind]:
            self.problem("duplicate-core-field", where, f"a command has one {block.__name__} field")
            return
        seen.blocks[block.field_kind] += 1
        default = info.default if isinstance(info.default, block) else block()
        refresh_help: str | None = None
        if isinstance(default, Output):
            self._output_field(where, info.annotation, default)
        elif isinstance(default, Stdin):
            self._stdin_field(where, info.annotation, default)
        elif isinstance(default, Refresh):
            refresh_help = self._refresh_field(where, default)
        elif (
            isinstance(default, Parallel)
            and isinstance(default.default, SettingKey)
            and SettingKey.model_of(default.default) is not getattr(self.plugin, "settings", None)
        ):
            self.problem(
                "settings-model", where, "Parallel's default reads another plugin's settings"
            )
        for option in block.options:
            choices: tuple[str, ...] = ()
            if option.flag == "--format" and isinstance(default, Output):
                choices = default.formats
            seen.options.append(
                OptionDecl(
                    field=path,
                    flag=option.flag,
                    metavar=option.flag.removeprefix("--").upper(),
                    token=option.token,
                    choices=choices,
                    negative=option.negative,
                    short=option.short,
                    help=refresh_help
                    or default._option_help(option),  # Refresh's one flag pair, about its caches
                    panel=block.panel,
                    block=block.field_kind,
                    default=default._option_default(option),
                )
            )

    def _output_field(self, where: str, annotation: object, default: Output[Any]) -> None:
        model = generic_argument(annotation)
        if not _is_model(model):
            self.problem(
                "field-type", where, "Output[R] takes the pydantic model of the records printed"
            )
            return
        self.seen.output = model
        self.seen.formats = default.formats
        self._kind(model)

    def _stdin_field(self, where: str, annotation: object, default: Stdin[Any]) -> None:
        accepted = generic_argument(annotation)
        models = [accepted] if _is_model(accepted) else list(get_args(accepted))
        if not models or not all(_is_model(model) for model in models):
            self.problem(
                "field-type", where, "Stdin[R] takes the pydantic model(s) of the rows accepted"
            )
            return
        self.seen.stdin.extend(target_of(model) for model in models)
        if default.via is not None:
            self.seen.stdin_via = self._via(where, default.via, tuple(models))

    def _via(self, where: str, via: object, models: tuple[type[BaseModel], ...]) -> str | None:
        """The contract whose bridge ``Stdin(via=)`` names, if it names one returning ``R``."""
        from untaped.sdk.contracts._declare import method_contract  # noqa: PLC0415

        try:
            info, method = method_contract(typing.cast("collections.abc.Callable[..., Any]", via))
        except TypeError:
            info = None
        if info is None or not method.bridge:
            self.problem(
                "field-type",
                where,
                f"Stdin(via=) takes a contract's bridge method (BookSource.to_book), not {via!r}",
            )
            return None
        returned = method.hints["return"]
        if not issubclass(returned, models):
            names = " | ".join(model.__name__ for model in models)
            self.problem(
                "field-type",
                where,
                f"Stdin[{names}](via={info.cls.__name__}.{method.name}) reads "
                f"{returned.__name__} rows, which it doesn't accept",
            )
            return None
        return target_of(info.cls)

    def _refresh_field(self, where: str, default: Refresh) -> str | None:
        """Check the caches ``default`` names; the help of its flags, or ``None`` on a problem.

        The help names each cache's label and max-age setting, and goes into
        the declaration, so help never imports the command to say it.
        """
        if not default.caches:
            self.problem(
                "refresh-cache",
                where,
                "Refresh names the caches its flags are about: Refresh(CACHE)",
            )
            return None
        named: list[tuple[Cache[Any], str]] = []
        for each in default.caches:
            if isinstance(each, Cache) and each.version_only:
                self.problem(
                    "refresh-cache",
                    where,
                    f"{each!r} is version-only: it has no max age for --refresh to override",
                )
                continue
            attribute = self.plugin.held.get(id(each))
            if isinstance(each, Cache) and attribute is not None:
                setting = _max_age_setting(self.plugin.name, each.derived_name(attribute))
                named.append((each, setting))
                continue
            cache = self._cached_method(each)
            if cache is not None:
                spelled = self._setting_of(cache, each)
                if spelled is not None:
                    named.append((cache, spelled))
                    continue
                self.problem(
                    "unowned-cache",
                    where,
                    f"{each!r}'s cache {cache!r} belongs to no composed plugin: spell it "
                    "Cache(owner, name, ...) or hold it on the owner's plugin class",
                )
                continue
            self.problem(
                "refresh-cache",
                where,
                f"{each!r} is neither a cache on the plugin class nor a cached method of a "
                "contract the plugin offers or fills",
            )
        return _refresh_help(named) if len(named) == len(default.caches) else None

    def _setting_of(self, cache: Cache[Any], method: object) -> str | None:
        """The max-age setting of a contract method's cache; ``None`` when no plugin owns it.

        A cache spelled or bound where declared knows its plugin; one
        ``@cached`` made belongs to the plugin offering the method's contract;
        one a plugin class holds belongs to the plugin composed for that
        class's package, read here because no run exists yet.
        """
        if cache.spelled or cache._held_as is not None:
            return cache.setting
        if cache._declared_as is not None and not cache.bindings:
            from untaped.sdk.contracts._declare import method_contract  # noqa: PLC0415

            contract = method_contract(typing.cast("collections.abc.Callable[..., Any]", method))[
                0
            ].cls
            plugin = self.plugin.offering.get(contract)
            return None if plugin is None else _max_age_setting(plugin, cache._declared_as)
        for binding in cache.bindings:
            plugin = self.plugin.owners.get(binding.owner.__module__.partition(".")[0])
            if plugin is not None:
                return _max_age_setting(plugin, cache.derived_name(binding.attribute))
        return None

    def _cached_method(self, method: object) -> Cache[Any] | None:
        """The answer cache of ``method`` if it is a cached method the plugin offers or fills."""
        cache = getattr(method, "__untaped_cached__", None)
        if not isinstance(cache, Cache):
            return None
        name = getattr(method, "__name__", "")
        classes = (*self.plugin.offers, *self.plugin.fills)
        return cache if any(getattr(cls, name, None) is method for cls in classes) else None

    def _duplicates(self) -> None:
        flags: dict[str, str] = {}
        for option in self.seen.options:
            for flag in (option.flag, option.short, option.negative):
                if flag is None:
                    continue
                if flag in flags and flags[flag] != option.field:
                    self.problem(
                        "duplicate-field",
                        f"{self.where}.{option.field}",
                        f"{flag} is also {self.where}.{flags[flag]}'s",
                    )
                flags.setdefault(flag, option.field)

    def _positionals(self) -> None:
        """Arguments fill in field order: none may follow an optional or a list one."""
        before: OptionDecl | None = None
        for option in (each for each in self.seen.options if each.positional):
            if before is not None and before.token.startswith("list["):
                self.problem(
                    "positional-order",
                    f"{self.where}.{option.field}",
                    f"follows {before.field}, a list argument that takes every value left",
                )
            elif before is not None and option.required and not before.required:
                self.problem(
                    "positional-order",
                    f"{self.where}.{option.field}",
                    f"is required but follows {before.field}, an optional argument",
                )
            before = option


# --- types -----------------------------------------------------------------------------


def _unwrap(annotation: object) -> tuple[object, bool, tuple[object, ...]]:
    """``(value type, repeated, marks)`` of a field annotation.

    ``Annotated[list[str], Positional] | None`` is ``(str, True, (POSITIONAL,))``:
    pydantic lifts only top-level ``Annotated`` marks into the field's metadata,
    so the marks inside ``X | None`` or a list are collected here.
    """
    marks: list[object] = []

    def peel(annotation: object) -> object:
        while get_origin(annotation) is typing.Annotated:
            annotation, *found = get_args(annotation)
            marks.extend(found)
        return annotation

    if get_origin(annotation) in (typing.Union, types.UnionType):
        members = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(members) == 1:
            annotation = members[0]
    annotation = peel(annotation)
    repeated = False
    if get_origin(annotation) in (
        list,
        tuple,
        set,
        frozenset,
        collections.abc.Sequence,
    ) and not _is_pair(annotation):
        args = [arg for arg in get_args(annotation) if arg is not Ellipsis]
        if len(args) == 1:
            annotation, repeated = args[0], True
    return peel(annotation), repeated, tuple(marks)


def _is_pair(annotation: object) -> bool:
    """``tuple[str, str]`` and ``tuple[str, JsonValue]``: ``KeyValue`` and ``KeyJson``."""
    args = get_args(annotation)
    return get_origin(annotation) is tuple and len(args) == 2 and Ellipsis not in args


def _token(value: object) -> tuple[str, tuple[str, ...]] | None:
    """The token shape the parser converts ``value`` from, with its choices."""
    simple: dict[object, str] = {bool: "bool", int: "int", float: "float", str: "str"}
    if value in simple:
        return simple[value], ()
    if isinstance(value, type) and issubclass(value, PurePath):
        return "str", ()
    if get_origin(value) is typing.Literal:
        choices = get_args(value)
        token = (
            "int" if all(isinstance(c, int) and not isinstance(c, bool) for c in choices) else "str"
        )
        return token, tuple(str(c) for c in choices)
    if isinstance(value, type) and issubclass(value, enum.Enum):
        return "str", tuple(str(member.value) for member in value)
    if get_origin(value) is File or value is File or _is_pair(value):
        return "str", ()
    return None


def _shape(hint: object) -> tuple[Returns, type[BaseModel] | None] | None:
    """What a return type says the command prints, and the record model it names."""
    shapes: dict[object, Returns] = {None: "nothing", type(None): "nothing", str: "line"}
    if hint in shapes:
        return shapes[hint], None
    if _is_model(hint):
        return "record", hint
    origin = get_origin(hint)
    if origin in _ITERATORS:
        model = get_args(hint)[0] if get_args(hint) else None
        return ("rows", model) if _is_model(model) else None
    if origin not in (typing.Union, types.UnionType):
        return None
    members = [arg for arg in get_args(hint) if arg is not type(None)]
    has_none = len(members) < len(get_args(hint))
    if has_none and members == [str]:
        return "line-or-none", None
    if has_none and len(members) == 1 and _is_model(members[0]):
        return "record-or-none", members[0]
    if not has_none and len(members) == 2:
        records = [m for m in members if _is_model(m)]
        rows = [m for m in members if get_origin(m) in _ITERATORS and get_args(m) == tuple(records)]
        if len(records) == 1 and rows:
            return "rows-or-record", records[0]
    return None


def _nested_block(annotation: object) -> bool:
    """Whether a core-filled field type sits inside ``annotation`` (``Output[R] | None``)."""
    return any(
        field_block(arg) is not None or _nested_block(arg)
        for arg in get_args(annotation)
        if not isinstance(arg, str)
    )


def _jsonable(value: object) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    try:
        return to_jsonable_python(value)
    except Exception:
        return None
