"""``apply``, ``remove`` and ``unsubscribe``: place, skip or take back each path.

``apply`` places every selected path whatever its policy: ``pending``,
``foreign``, ``behind`` and ``missing`` paths are placed, ``orphan`` ones
taken back, and ``modified`` or ``conflict`` ones refused unless ``--force``
(the local version is kept aside). Taking a path back deletes a link or an
unedited copy, takes merged keys back out, and keeps an edited file aside.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import Failure, UntapedError
from untaped_dotfiles.errors import DotfilesError
from untaped_dotfiles.models import PlacedRecord
from untaped_dotfiles.records import ApplyAction, ApplyOutcome
from untaped_dotfiles.states import Action, target_modified

if TYPE_CHECKING:
    from untaped_dotfiles.inventory import Inventory, Placement
    from untaped_dotfiles.ports import DotfilesStore, Placer
    from untaped_dotfiles.status import Evaluation, Evaluator


@dataclass(frozen=True)
class Step:
    """What will happen to one placement."""

    placement: Placement
    found: Evaluation
    action: Action | None
    """``None``: refused (a ``modified`` or ``conflict`` path without ``--force``)."""
    detail: str = ""


class Applier:
    """Plans and runs placements, and takes placed paths back."""

    def __init__(
        self,
        store: DotfilesStore,
        placer: Placer,
        inventory: Inventory,
        evaluator: Evaluator,
        *,
        now: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._placer = placer
        self._inventory = inventory
        self._evaluator = evaluator
        self._now = now

    # -- planning ------------------------------------------------------------

    def plan_apply(self, placements: Sequence[Placement], *, force: bool) -> list[Step]:
        """Every placed path, whatever its policy; local edits need ``--force``."""
        self._evaluator.prime_changes(placements)
        steps: list[Step] = []
        for placement in placements:
            found = self._evaluator.evaluate(placement)
            steps.append(Step(placement, found, *self._apply_action(found, force=force)))
        return steps

    @staticmethod
    def _apply_action(found: Evaluation, *, force: bool) -> tuple[Action | None, str]:
        state = found.state
        if found.error is not None:
            return "report", found.detail
        if state in ("pending", "foreign", "behind", "missing"):
            return "apply", found.detail
        if state == "applied":
            return "skip", "already applied"
        if state == "orphan":
            return "remove", found.detail
        if force:
            return "apply", f"{found.detail}; the local version is kept aside"
        return None, f"{found.detail}; pass --force to replace it (kept aside)"

    # -- executing -----------------------------------------------------------

    def planned(self, steps: Sequence[Step]) -> list[ApplyOutcome]:
        """The preview rows: what each step would do; a step that cannot is failed, as in a run."""
        rows: list[ApplyOutcome] = []
        for step in steps:
            action = self._planned_action(step)
            error = step.found.error if action == "failed" else None
            rows.append(self._row(step, action, step.detail, error=error))
        return rows

    def execute(self, steps: Sequence[Step]) -> list[ApplyOutcome]:
        rows = [self._run(step) for step in steps]
        self._evaluator.reset()
        return rows

    @staticmethod
    def _planned_action(step: Step) -> ApplyAction:
        if step.action is None:
            return "conflict"
        if step.found.error is not None:
            return "failed"
        if step.action in ("apply", "remove"):
            return "planned"
        return "unchanged" if step.found.state == "applied" else "skipped"

    def _run(self, step: Step) -> ApplyOutcome:
        action = self._planned_action(step)
        if action == "failed":
            return self._row(step, "failed", step.detail, error=step.found.error)
        if action != "planned":
            return self._row(step, action, step.detail)
        try:
            if step.action == "remove":
                record = step.found.record
                kept = self._take_back(record) if record is not None else None
                gone = "taken back; the source no longer has it"
                return self._row(
                    step, "deleted", f"{gone}; kept the edited version at {kept}" if kept else gone
                )
            kept = self._place(step)
            done: ApplyAction = "created" if step.found.record is None else "updated"
            return self._row(step, done, f"kept the previous version at {kept}" if kept else "")
        except (UntapedError, OSError) as exc:
            failure = exc if isinstance(exc, UntapedError) else DotfilesError(str(exc))
            return self._row(
                step,
                "failed",
                str(failure),
                error=Failure.from_exception(failure, message=str(failure)),
            )

    @staticmethod
    def _row(
        step: Step, action: ApplyAction, detail: str, *, error: Failure | None = None
    ) -> ApplyOutcome:
        p = step.placement
        assert p.target is not None  # a placement waiting on an input is never planned
        return ApplyOutcome(
            source=p.source.name,
            package=p.package,
            item=p.item,
            entry=p.shown,
            mode=p.mode,
            action=action,
            state=step.found.state,
            detail=detail,
            target_path=p.target,
            error=error,
        )

    def _place(self, step: Step) -> Path | None:
        """Place one path; returns where a replaced local file was kept."""
        p, found = step.placement, step.found
        assert p.target is not None
        record = found.record
        target = p.target
        owner = (p.source.name, p.package, p.item)
        ours = record is not None and found.state in ("behind", "applied", "missing")
        present = target.is_symlink() or target.exists()
        kept: Path | None = None
        if p.mode == "merge":
            if present and not ours:
                kept = self._placer.keep_aside(target, owner=owner, copy=True)
            data = p.source.tree.read(p.path)
            assert p.fmt is not None  # checked when the package loads
            target_hash, managed = self._placer.merge(data, target, fmt=p.fmt)
            source_hash = self._evaluator.source_hash(p, data)
        else:
            if present and not ours:
                kept = self._placer.keep_aside(target, owner=owner)
            managed = ()
            if p.mode == "link":
                destination = self._inventory.link_destination(p.source, p.path)
                target_hash = self._placer.link(destination, target)
                source_hash = target_hash
            else:
                data = p.source.tree.read(p.path)
                target_hash = self._placer.copy(data, target, executable=p.executable)
                source_hash = self._evaluator.source_hash(p, data)
        self._store.put_placed(
            PlacedRecord(
                target=p.key,
                source=p.source.name,
                package=p.package,
                item=p.item,
                path=p.path,
                entry=p.entry,
                placed_as=p.mode,
                fmt=p.fmt,
                source_commit=found.commit,
                source_hash=source_hash,
                target_hash=target_hash,
                managed=managed,
                applied_at=self._now(),
            )
        )
        return kept

    # -- taking back ---------------------------------------------------------

    def _take_back(self, record: PlacedRecord) -> Path | None:
        """Take back what untaped placed; returns where an edited target was kept.

        A target that is no longer what untaped wrote is kept aside instead of
        deleted; a merge target whose managed keys were edited is copied aside
        before the keys go.
        """
        target = Path(record.target)
        owner = (record.source, record.package, record.item)
        kept: Path | None = None
        present = target.is_symlink() or target.exists()
        if record.placed_as == "merge":
            fmt = record.fmt
            if present and fmt is not None:
                observed = self._placer.observe(target, fmt=fmt, managed=record.managed)
                if target_modified(record.placed_as, record, observed):
                    kept = self._placer.keep_aside(target, owner=owner, copy=True)
                self._placer.unmerge(target, fmt=fmt, managed=record.managed)
        elif present:
            if target_modified(record.placed_as, record, self._placer.observe(target)):
                kept = self._placer.keep_aside(target, owner=owner)
            else:
                self._placer.delete(target)
        self._store.remove_placed(record.target)
        return kept

    def removal(self, records: Sequence[PlacedRecord], *, keep_files: bool) -> list[ApplyOutcome]:
        """The preview of taking ``records`` back (or forgetting them, with ``keep_files``)."""
        return [
            self._removal_row(
                record,
                "planned",
                "forgets it; the file stays" if keep_files else self._what(record),
            )
            for record in records
        ]

    def take_back(self, records: Sequence[PlacedRecord], *, keep_files: bool) -> list[ApplyOutcome]:
        """Take every one of ``records`` back (or only forget it, with ``keep_files``)."""
        rows: list[ApplyOutcome] = []
        for record in records:
            if keep_files:
                self._store.remove_placed(record.target)
                rows.append(self._removal_row(record, "deleted", "forgotten; the file stays"))
                continue
            try:
                kept = self._take_back(record)
            except (UntapedError, OSError) as exc:
                failure = exc if isinstance(exc, UntapedError) else DotfilesError(str(exc))
                message = str(failure)
                rows.append(
                    self._removal_row(
                        record,
                        "failed",
                        message,
                        error=Failure.from_exception(failure, message=message),
                    )
                )
                continue
            rows.append(
                self._removal_row(
                    record, "deleted", f"kept the edited version at {kept}" if kept else ""
                )
            )
        self._evaluator.reset()
        return rows

    def _what(self, record: PlacedRecord) -> str:
        """What taking ``record`` back does to its target, as things stand."""
        target = Path(record.target)
        if not (target.is_symlink() or target.exists()):
            return "forgets it; the target is already gone"
        observed = self._placer.observe(target, fmt=record.fmt, managed=record.managed)
        edited = target_modified(record.placed_as, record, observed)
        if record.placed_as == "merge":
            return "takes the merged keys back" + (
                " (the edited file is kept aside first)" if edited else ""
            )
        if edited:
            return "keeps the edited file aside"
        return "deletes the link" if record.placed_as == "link" else "deletes the copy"

    def _removal_row(
        self,
        record: PlacedRecord,
        action: ApplyAction,
        detail: str,
        *,
        error: Failure | None = None,
    ) -> ApplyOutcome:
        target = Path(record.target)
        return ApplyOutcome(
            source=record.source,
            package=record.package,
            item=record.item,
            entry=self._inventory.shown(target),
            mode=record.placed_as,
            action=action,
            detail=detail,
            target_path=target,
            error=error,
        )
