"""``dotfiles status``: the state of every placement, offline, and the summary of the rows."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from untaped.sdk import Failure, UntapedError
from untaped_dotfiles.errors import DotfilesError
from untaped_dotfiles.hashing import content_hash, value_hash
from untaped_dotfiles.records import EntryStatus, StatusSummary
from untaped_dotfiles.states import FileState, SourceInfo, file_state, needs_attention

if TYPE_CHECKING:
    from untaped_dotfiles.inventory import Inventory, Placement
    from untaped_dotfiles.models import PlacedRecord
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

    def reset(self) -> None:
        """Forget what was read (after a move or a placement)."""
        self._changed.clear()
        self._change_errors.clear()
        self._heads.clear()
        self._records = None

    def record(self, placement: Placement) -> PlacedRecord | None:
        if self._records is None:
            self._records = {r.target: r for r in self._store.placed()}
        return self._records.get(placement.key)

    def prime_changes(self, placements: Sequence[Placement]) -> None:
        """Read, once per clone, which link sources the fetched commit changed."""
        by_source: dict[str, set[str]] = {}
        sources: dict[str, Source] = {}
        for p in placements:
            if p.mode == "link" and p.source.clone is not None and not p.excluded:
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

    def source(self, placement: Placement) -> SourceInfo:
        source = placement.source
        if placement.orphan:  # the package no longer places it, whatever the tree holds
            return SourceInfo(exists=False)
        if placement.mode == "link":
            destination = self._inventory.link_destination(source, placement.path)
            in_tree = destination.exists() or destination.is_symlink()
            changed = placement.path in self._changed.get(source.name, set())
            if not in_tree and source.clone is not None:
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
        name = placement.source.name
        failed = self._change_errors.get(name) if placement.mode == "link" else None
        try:
            if failed is not None:
                raise failed
            source = self.source(placement)
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
        state, detail = file_state(placement.mode, record, source, target)
        error = None
        if state == "orphan" and record is None:
            failure = DotfilesError(f"{placement.path}: {detail}", category="not_found")
            error = Failure.from_exception(failure, message=detail)
        if placement.mode != "link":
            commit = placement.source.tree.commit
        elif placement.source.clone is not None:
            commit = self._head(placement.source)
        else:
            commit = None
        return Evaluation(state, detail, record, source.hash, commit, error=error)

    def _head(self, source: Source) -> str | None:
        if source.name not in self._heads:
            clone = source.clone
            assert clone is not None
            self._heads[source.name] = self._clones.checked_out(clone)
        return self._heads[source.name]


class StatusReader:
    """One :class:`EntryStatus` per placement, and the summary over them."""

    def __init__(self, evaluator: Evaluator, *, now: Callable[[], datetime]) -> None:
        self._evaluator = evaluator
        self._now = now

    def rows(self, placements: Sequence[Placement]) -> list[EntryStatus]:
        self._evaluator.prime_changes(placements)
        rows: list[EntryStatus] = []
        for placement in placements:
            found = self._evaluator.evaluate(placement)
            rows.append(
                EntryStatus(
                    source=placement.source.name,
                    package=placement.package,
                    item=placement.item,
                    entry=placement.shown,
                    mode=placement.mode,
                    policy=placement.policy,
                    chosen=placement.chosen,
                    state=found.state,
                    detail=found.detail,
                    target_path=placement.target,
                    error=found.error,
                )
            )
        return rows

    def summary(self, rows: Sequence[EntryStatus]) -> StatusSummary:
        counted = Counter(row.state for row in rows if row.state != "excluded")
        attention = sum(
            1 for row in rows if needs_attention(row.policy, row.state) or row.error is not None
        )
        return StatusSummary(
            checked_at=self._now(),
            total=sum(counted.values()),
            attention=attention,
            pending=counted["pending"],
            foreign=counted["foreign"],
            applied=counted["applied"],
            behind=counted["behind"],
            modified=counted["modified"],
            conflict=counted["conflict"],
            missing=counted["missing"],
            orphan=counted["orphan"],
            error=counted["error"],
        )
