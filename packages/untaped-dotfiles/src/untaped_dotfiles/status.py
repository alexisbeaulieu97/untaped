"""``dotfiles status``: the state of every placement, offline, and the summary of the rows."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from untaped.sdk import Failure, UntapedError, run_hint
from untaped_dotfiles.errors import DotfilesError
from untaped_dotfiles.hashing import content_hash, value_hash
from untaped_dotfiles.records import EntryStatus, StatusSummary
from untaped_dotfiles.states import Action, FileState, SourceInfo, decide, file_state

if TYPE_CHECKING:
    from untaped_dotfiles.inventory import Inventory, Placement
    from untaped_dotfiles.models import PlacedRecord
    from untaped_dotfiles.package import Mode
    from untaped_dotfiles.ports import DotfilesStore, Placer
    from untaped_dotfiles.sources import Clones, Source


@dataclass(frozen=True)
class Evaluation:
    """One placement's state and what placing it would record about its source."""

    state: FileState
    detail: str
    record: PlacedRecord | None
    source_hash: str | None
    commit: str | None
    error: Failure | None = None
    converts: bool = False
    """A link whose item no longer follows ``sync``: ``apply`` makes it a copy of what it shows
    before the clone moves."""


#: The detail of a ``new`` entry.
ADDED = "added to the package since it was applied here"


class Evaluator:
    """Computes :class:`Evaluation` for placements, reading each clone's changes once."""

    def __init__(
        self, store: DotfilesStore, clones: Clones, placer: Placer, inventory: Inventory
    ) -> None:
        self._store = store
        self._clones = clones
        self._placer = placer
        self._inventory = inventory
        self._changed: dict[str, set[str]] = {}
        self._change_errors: dict[str, UntapedError] = {}
        self._heads: dict[str, str | None] = {}
        self._records: dict[str, PlacedRecord] | None = None
        self._applied: dict[tuple[str, str, str], bool] = {}

    def reset(self) -> None:
        """Forget what was read (after a move or a placement)."""
        self._changed.clear()
        self._change_errors.clear()
        self._heads.clear()
        self._records = None
        self._applied.clear()

    def record(self, placement: Placement) -> PlacedRecord | None:
        if self._records is None:
            self._records = {r.target: r for r in self._store.placed()}
        return self._records.get(placement.key)

    def mode(self, placement: Placement) -> Mode:
        """How ``placement`` is placed now: as its record says when it switches between a link
        and a copy (a policy change converts it), else as its policy places it."""
        record = self.record(placement)
        if record is not None and {record.placed_as, placement.placed_as} == {"link", "copy"}:
            return record.placed_as
        return placement.placed_as

    def _first_applied(self, placement: Placement) -> bool:
        owner = placement.owner
        if owner not in self._applied:
            self._applied[owner] = self._store.first_applied(owner)
        return self._applied[owner]

    def prime_changes(self, placements: Sequence[Placement]) -> None:
        """Read, once per clone, which link sources the fetched commit changed."""
        by_source: dict[str, set[str]] = {}
        sources: dict[str, Source] = {}
        for p in placements:
            if p.source.clone is not None and not (p.excluded or p.off) and self.mode(p) == "link":
                by_source.setdefault(p.source.name, set()).add(p.path)
                sources[p.source.name] = p.source
        for name, paths in by_source.items():
            if name in self._changed:
                continue
            clone = sources[name].clone
            assert clone is not None
            try:
                self._changed[name] = self._clones.changed(clone, sorted(paths))
            except UntapedError as exc:  # reported on each link row of the source, below
                self._changed[name] = set()
                self._change_errors[name] = exc

    def source(self, placement: Placement, mode: Mode | None = None) -> SourceInfo:
        source = placement.source
        if placement.orphan:  # the package no longer places it, whatever the tree holds
            return SourceInfo(exists=False)
        if (mode or placement.placed_as) == "link":
            destination = self._inventory.link_destination(source, placement.path)
            in_tree = destination.exists() or destination.is_symlink()
            changed = placement.path in self._changed.get(source.name, set())
            if not in_tree and (source.clone is not None or source.peeked):
                in_tree = source.tree.exists(placement.path)
                changed = changed or in_tree
            return SourceInfo(exists=in_tree, hash=str(destination), changed=changed)
        tree = source.tree
        if not tree.exists(placement.path) or tree.is_dir(placement.path):
            return SourceInfo(exists=False)
        data = tree.read(placement.path)
        return SourceInfo(exists=True, hash=self.source_hash(placement, data))

    def source_hash(self, placement: Placement, data: bytes) -> str:
        if placement.mode == "merge" and placement.fmt is not None:
            try:
                return value_hash(self._placer.parse(data, fmt=placement.fmt))
            except DotfilesError:
                return content_hash(data)
        return content_hash(data)

    def evaluate(self, placement: Placement) -> Evaluation:
        if placement.excluded is not None:
            return Evaluation("excluded", placement.excluded, None, None, None)
        if placement.target is None:
            return Evaluation("pending", placement.unanswered or "", None, None, None)
        record = self.record(placement)
        if placement.off:
            return Evaluation("off", _off_detail(placement), record, None, None)
        name = placement.source.name
        mode = self.mode(placement)
        failed = self._change_errors.get(name) if mode == "link" else None
        try:
            if failed is not None:
                raise failed
            source = self.source(placement, mode)
            target = self._placer.observe(
                placement.target,
                fmt=placement.fmt,
                managed=record.managed if record is not None else (),
            )
        except UntapedError as exc:
            return Evaluation(
                "error",
                str(exc),
                record,
                None,
                None,
                error=Failure.from_exception(exc, message=str(exc)),
            )
        state, detail = file_state(mode, record, source, target)
        error = None
        if state == "orphan" and record is None:
            failure = DotfilesError(f"{placement.path}: {detail}", category="not_found")
            error = Failure.from_exception(failure, message=detail)
        state, detail, converts = self._refined(placement, mode, state, detail)
        if mode != "link" or placement.source.peeked:
            commit = placement.source.tree.commit
        elif placement.source.clone is not None:
            commit = self._head(placement.source)
        else:
            commit = None
        return Evaluation(
            state, detail, record, source.hash, commit, error=error, converts=converts
        )

    def _refined(
        self, placement: Placement, mode: Mode, state: FileState, detail: str
    ) -> tuple[FileState, str, bool]:
        """``(state, detail, converts)``: what the item's history and policy add to the state
        the files give (a ``new`` entry, a moved target, a link or copy to convert)."""
        if state == "orphan" and placement.moved:
            return state, "an input moved its target", False
        unrecorded = state in ("pending", "foreign") and not placement.asked
        if unrecorded and self._first_applied(placement):
            return "new", f"{ADDED}; {detail}" if detail else ADDED, False
        if mode == placement.placed_as or state not in ("applied", "behind"):
            return state, detail, False
        policy = placement.policy
        if placement.placed_as == "link":  # a copy whose item follows sync now
            return "behind", f"becomes a link: {policy} places links", False
        becomes = f"becomes a copy of what it shows: {policy} places copies"
        return state, f"{becomes}; {detail}" if state == "behind" else becomes, True

    def _head(self, source: Source) -> str | None:
        if source.name not in self._heads:
            clone = source.clone
            assert clone is not None
            self._heads[source.name] = self._clones.checked_out(clone)
        return self._heads[source.name]


def _off_detail(placement: Placement) -> str:
    from untaped_dotfiles.commands.packages import Remove  # noqa: PLC0415 - a cycle

    offer = placement.offer
    which = "package" if not offer.on else "item"
    spelled = offer.spelled if not offer.on else placement.spelled
    remove = run_hint(Remove.spelled(spelled))
    return f"placed earlier; the {which} is off now; to take it back, {remove}"


@dataclass(frozen=True)
class Reading:
    """The status rows, and what ``apply`` with no selector would do with each."""

    rows: list[EntryStatus]
    actions: list[Action]


class StatusReader:
    """One :class:`EntryStatus` per placement, and the summary over them."""

    def __init__(self, evaluator: Evaluator, *, now: Callable[[], datetime]) -> None:
        self._evaluator = evaluator
        self._now = now

    def read(self, placements: Sequence[Placement]) -> Reading:
        self._evaluator.prime_changes(placements)
        rows: list[EntryStatus] = []
        actions: list[Action] = []
        for placement in placements:
            found = self._evaluator.evaluate(placement)
            rows.append(
                EntryStatus(
                    source=placement.source.name,
                    package=placement.package,
                    item=placement.item,
                    entry=placement.shown,
                    mode=self._evaluator.mode(placement),
                    policy=placement.policy,
                    chosen=placement.chosen,
                    state=found.state,
                    detail=found.detail,
                    target_path=placement.target,
                    error=found.error,
                )
            )
            waits = found.error is not None or (
                placement.target is None and placement.excluded is None
            )  # an error, or an input nothing answers: only a person settles it
            actions.append(
                "report" if waits else decide(placement.policy, found.state, moved=placement.moved)
            )
        return Reading(rows, actions)

    def summary(self, reading: Reading, *, problems: int = 0) -> StatusSummary:
        """The counts over ``reading``; ``problems`` (packages or sources that could not be
        read) count as entries in ``error``, so a prompt segment sees them. ``apply`` counts the
        rows the next ``apply`` changes on its own, ``attention`` those only a person settles."""
        counted = Counter(row.state for row in reading.rows if row.state != "excluded")
        counted["error"] += problems
        return StatusSummary(
            checked_at=self._now(),
            total=sum(counted.values()),
            apply=sum(1 for action in reading.actions if action in ("apply", "remove")),
            attention=problems + sum(1 for action in reading.actions if action == "report"),
            pending=counted["pending"],
            foreign=counted["foreign"],
            new=counted["new"],
            applied=counted["applied"],
            behind=counted["behind"],
            modified=counted["modified"],
            conflict=counted["conflict"],
            missing=counted["missing"],
            orphan=counted["orphan"],
            off=counted["off"],
            error=counted["error"],
        )
