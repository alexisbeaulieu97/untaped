"""Composition: from the plugin classes to the :class:`Declaration`, with its checks.

Core starts from each plugin class an entry point names and follows its
references (:class:`~untaped.runtime.plugin_walk.PluginWalk`, which walks
each command with :class:`~untaped.runtime.command_walk.CommandWalk`).
Everything it reaches is the plugin's, named after the attribute or class
under the plugin's name: commands from class names in kebab case, kinds
from the classes ``Output[R]`` and ``File[M]`` name, blocks from their
attributes. What the shape cannot prevent is checked in those walks, once,
and each problem names the class or field it is about. :func:`compose`
settles what plugins claim against each other; a plugin with any problem
is quarantined and the others compose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel

from untaped.runtime.declaration import (
    Declaration,
    KindDecl,
    PluginDecl,
    Problem,
    Quarantined,
    target_of,
)
from untaped.runtime.describe import (
    fields_of,
    first_paragraph,
    prose,
)
from untaped.runtime.plugin_walk import PluginWalk, is_plugin, package_of

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


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
            owners.setdefault(package_of(entries[i].target), entries[i].name)
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
    offers = getattr(entry.plugin, "offers", ()) if is_plugin(entry.plugin) else ()
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
    walks: dict[int, tuple[PluginEntry, PluginWalk]] = {}
    for index, entry in enumerate(entries):
        if index not in withdrawn:
            walks[index] = (
                entry,
                PluginWalk(entry, owners, _view(entries, index, offering, readable)),
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


def _names(
    walks: dict[int, tuple[PluginEntry, PluginWalk]], sound: list[int], taken: frozenset[str]
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
    cls: type[BaseModel] = field(compare=False, repr=False)


def _clash(claim: _Claim, other: _Claim) -> str | None:
    """Why ``claim`` cannot stand beside ``other``: two classes one kind, or one class two."""
    if claim.kind == other.kind and claim.target != other.target:
        return f"would be the kind {claim.kind}, which {other.target} is: rename one"
    if claim.target == other.target and claim.kind != other.kind:
        return f"would be the kind {claim.kind}, but it is {other.kind}: a record has one kind"
    return None


def _claims(index: int, walk: PluginWalk) -> list[_Claim]:
    """The kinds entry ``index`` claims for the records its commands and offers reach."""
    return [
        _Claim((_PHASES.index(phase), index), kind, target_of(cls), cls.__qualname__, cls)
        for cls, kind in walk.kinds.items()
        if (phase := walk.phase_of(cls)) != "provided"
    ]


def _own_clashes(walk: PluginWalk) -> list[Problem]:
    """Two of ``walk``'s records of one kind: the plugin is broken on its own."""
    problems: list[Problem] = []
    first: dict[str, _Claim] = {}
    for claim in _claims(0, walk):
        why = _clash(claim, first.setdefault(claim.kind, claim))
        if why is not None:
            problems.append(Problem(rule="duplicate-kind", where=claim.where, detail=why))
    return problems


def _settle(
    walks: dict[int, PluginWalk],
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
            claim = _Claim((len(_PHASES), index), walk.kinds[cls], target_of(cls), "", cls)
            if not any(_clash(claim, other) for other in kinds.values()):
                kinds[claim.kind] = claim
                holding.append(claim)
    return {
        index: {claim.kind: _kind_decl(claim) for claim in holding}
        for index, holding in held.items()
    }, clashes


def _kind_decl(claim: _Claim) -> KindDecl:
    """The kind ``claim`` holds, with its model's prose and fields for its page."""
    doc = prose(claim.cls)[1]
    return KindDecl(
        kind=claim.kind,
        target=claim.target,
        plugin=claim.kind.partition(".")[0],
        summary=first_paragraph(doc),
        doc=doc,
        fields=fields_of(claim.cls),
    )


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
