"""Placements: every path an item that is on puts on this machine, resolved from its package.

A folder entry is placed per child: the target folder stays a real folder,
each file under the entry gets its own placement at the same relative path,
and files other programs write there are never touched. A target renders
``{{ name }}`` from this run's answers (``--var``, ``--vars-file``, a
prompt), else this machine's (``inputs`` in the package's choice), else the
input's default; an input with none leaves its entries waiting, naming the
``add --var`` that answers it, and what the entry placed before stays as it
is.

A path untaped placed that its entry no longer places is an ``orphan``:
the entry left the package, the file left the source's folder, the input
moved the target (``moved``: the machine asked, so it goes whatever the
policy), or the package file left the source. A path whose entry is now
left out on this machine (``os``, ``only``, ``unless``) is ``excluded`` and
stays as it is; one whose package or item is off now is ``off``. Two
packages placing one target are an error for each, and neither is placed.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from untaped.sdk import UntapedError, q, run_hint
from untaped_dotfiles.errors import PackageError
from untaped_dotfiles.policy import Policy
from untaped_dotfiles.states import placed_as
from untaped_values.api import InputError, placeholders, render

if TYPE_CHECKING:
    from untaped_dotfiles.catalog import Catalog, Offer, Selection
    from untaped_dotfiles.models import Machine, PlacedRecord
    from untaped_dotfiles.package import File, MergeFormat, Mode
    from untaped_dotfiles.ports import DotfilesStore, SourceTree
    from untaped_dotfiles.sources import Source


@dataclass(frozen=True)
class Placement:
    """One path an item places (a file entry, or one child of a folder entry)."""

    offer: Offer
    item: str
    entry: str
    """The package file entry's ``path`` (a folder for its children)."""
    path: str
    """The source-relative path placed."""
    target: Path | None
    """``None`` while an input the target names has no value (``unanswered`` says which)."""
    shown: str
    """The target as the user reads it: ``~/...``, or the package's text while unrendered."""
    declared: str
    """The target as the package writes it (``{{ input }}`` unrendered)."""
    mode: Mode
    policy: Policy
    chosen: bool = False
    fmt: MergeFormat | None = None
    executable: bool = False
    excluded: str | None = None
    unanswered: str | None = None
    orphan: bool = False
    """A placed record the package or the tree no longer lists."""
    moved: bool = False
    """An ``orphan`` whose entry places elsewhere now: an input moved its target."""
    asked: bool = False
    """Asked for this run, so ``pending`` rather than ``new``: the new place of a ``moved``
    entry, or a target that names an input a flag or a prompt answered."""
    off: bool = False
    """A placed record whose package or item is off now."""

    @property
    def placed_as(self) -> Mode:
        """How the file is placed under its policy (:func:`~untaped_dotfiles.states.placed_as`)."""
        return placed_as(self.policy, self.mode)

    @property
    def spelled(self) -> str:
        """``SOURCE/PKG/ITEM``."""
        return f"{self.offer.spelled}/{self.item}"

    @property
    def source(self) -> Source:
        return self.offer.source

    @property
    def package(self) -> str:
        return self.offer.name

    @property
    def key(self) -> str:
        return str(self.target)

    @property
    def owner(self) -> tuple[str, str, str]:
        return (self.source.name, self.package, self.item)


@dataclass(frozen=True)
class Resolved:
    """The placements of some packages, and the packages that could not be resolved."""

    placements: list[Placement]
    problems: list[tuple[str, UntapedError]]
    """``(SOURCE/PKG, error)`` for each package that doesn't load."""


class Inventory:
    """Expands the items that are on into placements."""

    def __init__(
        self, catalog: Catalog, store: DotfilesStore, *, home: Path, machine: Machine
    ) -> None:
        self._catalog = catalog
        self._store = store
        self._home = home.resolve()
        self._machine = machine
        self._answers: dict[str, dict[str, object]] = {}
        self._asked: dict[str, frozenset[str]] = {}
        self._failed: dict[str, UntapedError] = {}

    def answer(self, offer: Offer, values: dict[str, object], *, asked: Iterable[str] = ()) -> None:
        """Use ``values`` for ``offer``'s inputs this run, over this machine's answers.

        ``asked``: the names a flag or a prompt answered this run; an entry
        whose target names one was asked for (``pending``, not ``new``).
        """
        self._answers[offer.spelled] = dict(values)
        self._asked[offer.spelled] = frozenset(asked)

    def fail(self, offer: Offer, error: UntapedError) -> None:
        """Report ``offer`` as ``error`` this run (an answer that doesn't fit its input): its
        files stay as they are and the other packages go on."""
        self._failed[offer.spelled] = error

    def values(self, offer: Offer) -> dict[str, object]:
        """``offer``'s plain top-level input values here: this run's answers, this machine's,
        else the defaults."""
        return input_values(offer) | self._answers.get(offer.spelled, {})

    @property
    def machine(self) -> Machine:
        return self._machine

    @property
    def home(self) -> Path:
        return self._home

    def shown(self, target: Path) -> str:
        """``target`` as the user reads it: under ``~/`` when it is in the home folder."""
        try:
            return "~/" + target.relative_to(self._home).as_posix()
        except ValueError:
            return str(target)

    def resolve_target(self, target: str) -> Path:
        """``target`` as an absolute path; ``~`` is this machine's home."""
        if target == "~":
            path = self._home
        elif target.startswith("~/"):
            path = self._home / target[2:]
        else:
            path = Path(target)
        path = Path(*path.parts)
        if not path.is_absolute():
            raise PackageError(f"target {target!r} must start with '~/' or '/'")
        if path == self._home or path in self._home.parents:
            raise PackageError(f"target {target!r} is the home folder or above it")
        return path

    def link_destination(self, source: Source, path: str) -> Path:
        return source.root / path

    def on(self) -> list[Selection]:
        """Every package that is on, as a selection."""
        from untaped_dotfiles.catalog import Selection  # noqa: PLC0415 - a cycle

        return [Selection(offer) for offer in self._catalog.offers() if offer.on]

    def placements(
        self, selections: Sequence[Selection] | None = None, *, include_excluded: bool = False
    ) -> Resolved:
        """The placements of ``selections`` (items that are on only; every package that is on
        when ``None``), plus what untaped placed for them that they no longer place
        (``orphan``), that is now left out (``excluded``), or whose package or item is off
        now (``off``: within the selections, or in any subscribed source when ``None``)."""
        everything = selections is None
        if selections is None:
            selections = self.on()
        found: list[Placement] = []
        problems: list[tuple[str, UntapedError]] = []
        resolved: dict[tuple[str, str, str], Offer] = {}
        held: dict[tuple[str, str, str, str], Placement | None] = {}
        """``(source, package, item, entry)`` whose records stay: excluded (its placement) or
        waiting on an input (``None``). Keyed by entry, so when two entries of one item share
        a path and one is held, the other's old records stay too (rather than orphaned)."""
        for offer, item in _wanted(selections):
            if offer.gone:
                resolved.update({owner: offer for owner in self._owners_of(offer)})
                continue
            if offer.package is None:
                assert offer.error is not None  # a package that doesn't load says why
                problems.append((offer.spelled, offer.error))
                continue
            failed = self._failed.get(offer.spelled)
            if failed is not None:
                if (offer.spelled, failed) not in problems:
                    problems.append((offer.spelled, failed))
                continue
            try:
                placed, waiting = self._item(offer, item)
            except UntapedError as exc:
                problems.append((f"{offer.spelled}/{item}", exc))
                continue
            resolved[(offer.source.name, offer.name, item)] = offer
            for each in waiting:
                held[(*each.owner, each.entry)] = each if each.excluded is not None else None
            found.extend(placed)
            if include_excluded:
                found.extend(p for p in waiting if p.excluded is not None)
        fought = self._fought()
        found, claimed = _claims(found, fought)
        problems.extend(claimed)
        found = self._with_records(
            found,
            resolved,
            held,
            fought,
            off=self._off_scope(selections, everything=everything),
            include_excluded=include_excluded,
        )
        return Resolved(found, problems)

    def _with_records(
        self,
        found: list[Placement],
        resolved: dict[tuple[str, str, str], Offer],
        held: dict[tuple[str, str, str, str], Placement | None],
        fought: dict[str, set[str]],
        *,
        off: dict[tuple[str, str, str], Offer],
        include_excluded: bool,
    ) -> list[Placement]:
        """``found``, plus a placement for each record it no longer places: ``orphan``
        (``moved`` when an input moved it), ``excluded`` or ``off``."""
        placed_keys = {p.key for p in found}
        bases = {
            (*p.owner, p.entry, p.declared): _base(p.target, p.entry, p.path)
            for p in found
            if p.target
        }
        """Where each declaration places now: a record of the same declaration placed
        elsewhere was moved by an input; one whose declaration changed is the author's edit."""
        asked: set[tuple[str, str, str, str, str]] = set()
        for record in self._store.placed():
            owner = (record.source, record.package, record.item)
            owned = resolved.get(owner)
            if record.target in placed_keys or record.target in fought:
                continue  # a disputed target keeps its file until the dispute is settled
            if owned is None:
                offer = off.get(owner) or off.get((record.source, record.package, ""))
                if offer is None:
                    continue
                if not offer.item_on(record.item):
                    found.append(self._from_record(record, offer, off=True))
                elif offer.package is not None and record.item not in offer.package.items:
                    found.append(self._from_record(record, offer, orphan=True))  # item left
                continue
            at = (*owner, record.entry)
            if at not in held:
                declared = (*at, record.declared)
                base = bases.get(declared)
                moved = base is not None and base != _base(
                    Path(record.target), record.entry, record.path
                )
                if moved:
                    asked.add(declared)
                found.append(self._from_record(record, owned, orphan=True, moved=moved))
                continue
            excluded = held[at]
            if excluded is not None:
                found.append(
                    self._from_record(
                        record, owned, excluded=excluded.excluded, declared=excluded.declared
                    )
                )
                if include_excluded:  # the record's own row stands for its entry
                    found = [p for p in found if p is not excluded]
        if asked:
            found = [
                replace(p, asked=True)
                if not p.orphan and (*p.owner, p.entry, p.declared) in asked
                else p
                for p in found
            ]
        return found

    def _off_scope(
        self, selections: Sequence[Selection], *, everything: bool
    ) -> dict[tuple[str, str, str], Offer]:
        """The packages (``(source, package, "")``) and items whose records count as ``off``
        when they are off: every subscribed package, or only what ``selections`` name."""
        if everything:
            return {(o.source.name, o.name, ""): o for o in self._catalog.offers()}
        return {(s.offer.source.name, s.offer.name, s.item or ""): s.offer for s in selections}

    def _fought(self) -> dict[str, set[str]]:
        """Each target two packages that are on place here, with their ``SOURCE/PKG`` names;
        every package that is on counts, whatever a run selects."""
        claimants: dict[str, set[str]] = {}
        for offer, item in _wanted(self.on()):
            if offer.package is None:
                continue
            try:
                placed, _ = self._item(offer, item)
            except UntapedError:  # said when the item is selected
                continue
            for p in placed:
                if p.target is not None:
                    claimants.setdefault(p.key, set()).add(offer.spelled)
        return {target: names for target, names in claimants.items() if len(names) > 1}

    def _owners_of(self, offer: Offer) -> set[tuple[str, str, str]]:
        """Every item ``offer``'s package placed something for (a package gone from its source)."""
        return {
            (r.source, r.package, r.item)
            for r in self._store.placed()
            if (r.source, r.package) == (offer.source.name, offer.name)
        }

    def _item(self, offer: Offer, item: str) -> tuple[list[Placement], list[Placement]]:
        """``(placements, waiting)``: what ``item`` places, and the entries it can't place here
        now (left out on this machine, or waiting on an input), unexpanded."""
        package = offer.package
        assert package is not None
        tree = offer.source.tree
        policy, chosen = offer.item_policy(item)
        assert policy is not None  # a package that loads has a policy
        placed: list[Placement] = []
        waiting: list[Placement] = []
        for file in package.items[item].files:
            reason = offer.exclusion(self._machine, item) or file.exclusion(
                self._machine.os, self._machine.tags
            )
            base = self._placement(offer, item, file, policy=policy, chosen=chosen)
            if reason is not None:
                waiting.append(replace(base, excluded=reason))
            elif base.target is None:
                placed.append(base)
                waiting.append(base)
            else:
                placed.extend(self._expand(base, tree))
        return placed, waiting

    def _placement(
        self, offer: Offer, item: str, file: File, *, policy: Policy, chosen: bool
    ) -> Placement:
        target, unanswered = self._render(offer, file.target)
        names = placeholders(file.target, field="target")
        return Placement(
            offer=offer,
            item=item,
            entry=file.path,
            path=file.path,
            target=target,
            shown=self.shown(target) if target is not None else file.target,
            declared=file.target,
            mode=file.mode,
            policy=policy,
            chosen=chosen,
            fmt=file.merge_format,
            unanswered=unanswered,
            asked=not self._asked.get(offer.spelled, frozenset()).isdisjoint(names),
        )

    def _render(self, offer: Offer, text: str) -> tuple[Path | None, str | None]:
        """The target ``text`` names here, or ``(None, why)`` while an input has no value."""
        package = offer.package
        assert package is not None
        values = self.values(offer)
        missing = [name for name in placeholders(text, field="target") if name not in values]
        if missing:
            return None, unanswered(offer, missing)
        try:
            rendered = render(text, values, package.inputs, field="target")
        except InputError as exc:
            raise PackageError(f"{offer.spelled}: {exc}") from exc
        return self.resolve_target(rendered), None

    def _expand(self, base: Placement, tree: SourceTree) -> list[Placement]:
        if not tree.is_dir(base.entry):
            return [base]
        if base.mode == "merge":
            raise PackageError(f"merge file {q(base.entry)}: the source is a folder")
        assert base.target is not None
        prefix = base.entry + "/"
        return [
            replace(
                base,
                path=child.path,
                target=(target := base.target / child.path.removeprefix(prefix)),
                shown=self.shown(target),
                executable=child.executable,
            )
            for child in tree.files(base.entry)
        ]

    def _from_record(
        self,
        record: PlacedRecord,
        offer: Offer,
        *,
        orphan: bool = False,
        moved: bool = False,
        off: bool = False,
        excluded: str | None = None,
        declared: str = "",
    ) -> Placement:
        """What untaped placed at ``record``'s target, as a placement of ``offer``."""
        policy, chosen = offer.item_policy(record.item)
        target = Path(record.target)
        return Placement(
            offer=offer,
            item=record.item,
            entry=record.entry,
            path=record.path,
            target=target,
            shown=self.shown(target),
            declared=declared,
            mode=record.placed_as,
            policy=policy or Policy.MANUAL,
            chosen=chosen,
            fmt=record.fmt,
            excluded=excluded,
            orphan=orphan,
            moved=moved,
            off=off,
        )


def _wanted(selections: Sequence[Selection]) -> list[tuple[Offer, str]]:
    """``(offer, item)`` for each item the selections name that is on, each once; a package
    gone from its source once, as ``(offer, "")``."""
    wanted: dict[tuple[str, str, str], tuple[Offer, str]] = {}
    for selection in selections:
        offer = selection.offer
        if offer.package is None:
            wanted.setdefault((offer.source.name, offer.name, ""), (offer, ""))
            continue
        items = [selection.item] if selection.item else list(offer.package.items)
        for item in items:
            if offer.item_on(item):
                wanted.setdefault((offer.source.name, offer.name, item), (offer, item))
    return list(wanted.values())


def _claims(
    placements: list[Placement], fought: dict[str, set[str]]
) -> tuple[list[Placement], list[tuple[str, UntapedError]]]:
    """``placements`` without the targets two packages claim (``fought``), and an error for
    each selected claimant."""
    selected: dict[str, set[str]] = {}
    for p in placements:
        if p.target is not None and p.excluded is None and p.key in fought:
            selected.setdefault(p.key, set()).add(p.offer.spelled)
    if not selected:
        return placements, []
    problems: list[tuple[str, UntapedError]] = []
    for target, mine in sorted(selected.items()):
        names = fought[target]
        for name in sorted(mine):
            problems.append(
                (
                    name,
                    PackageError(
                        f"target {target} is placed by {' and '.join(sorted(names))}; "
                        "neither places it",
                        category="conflict",
                    ),
                )
            )
    kept = [p for p in placements if p.target is None or p.excluded or p.key not in fought]
    return kept, problems


def input_values(offer: Offer) -> dict[str, object]:
    """The package's top-level input values here: this machine's answers, else the defaults."""
    package = offer.package
    if package is None:
        return {}
    answers = offer.choice.inputs if offer.choice is not None else {}
    values: dict[str, object] = {}
    for name, spec in package.inputs.items():
        if spec.secret:
            continue
        if name in answers:
            values[name] = answers[name]
        elif spec.default is not None:
            values[name] = spec.default
    return values


def unanswered(offer: Offer, names: Iterable[str]) -> str:
    """``input 'x' has no value: `untaped dotfiles add PKG --var x=VALUE```."""
    from untaped_dotfiles.commands.packages import Add  # noqa: PLC0415 - a cycle

    named = list(names)
    flags = [word for name in named for word in ("--var", f"{name}=VALUE")]
    which = ", ".join(q(name) for name in named)
    return f"input {which} has no value: {run_hint(Add.spelled(offer.spelled, *flags))}"


def _base(target: Path, entry: str, path: str) -> Path:
    """The target of entry ``entry`` itself, from the target of its file ``path`` (a child
    of a folder entry sits under it)."""
    if path == entry:
        return target
    depth = len(PurePosixPath(path).relative_to(entry).parts)
    return target.parents[depth - 1]
