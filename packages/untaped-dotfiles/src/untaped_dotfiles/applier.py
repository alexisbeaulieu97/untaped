"""``apply``, ``remove`` and ``unsubscribe``: place, skip or take back each path.

``apply`` places every selected path whatever its policy: ``pending``,
``foreign``, ``behind`` and ``missing`` paths are placed, ``orphan`` ones
taken back, and ``modified`` or ``conflict`` ones refused unless ``--force``
(the local version is kept aside). Taking a path back deletes a link or an
unedited copy, takes merged keys back out, and keeps an edited file aside.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import Failure, UntapedError, plural, run_hint, unified_diff_text
from untaped_dotfiles.errors import DotfilesError
from untaped_dotfiles.hashing import content_hash
from untaped_dotfiles.merge import leaf_paths
from untaped_dotfiles.models import CloneRecord, PlacedRecord
from untaped_dotfiles.records import ApplyAction, ApplyOutcome
from untaped_dotfiles.states import Action, decide, decide_asked, target_modified
from untaped_dotfiles.status import ADDED

if TYPE_CHECKING:
    from untaped_dotfiles.inventory import Inventory, Placement
    from untaped_dotfiles.package import Mode
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
    diff: str | None = None

    @property
    def converts(self) -> bool:
        """A link to make a copy of what it shows, before the clone moves."""
        return self.found.converts


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

    def plan_apply(
        self, placements: Sequence[Placement], *, force: bool, follow: bool
    ) -> list[Step]:
        """What ``apply`` does with each placement.

        ``follow``: by each entry's policy (no selector); else whatever the
        policy, with local edits refused unless ``force``.
        """
        self._evaluator.prime_changes(placements)
        steps: list[Step] = []
        for placement in placements:
            found = self._evaluator.evaluate(placement)
            action, detail = self._action(placement, found, force=force, follow=follow)
            step = Step(placement, found, action, detail)
            if action == "apply" and found.error is None:
                step = self._previewed(step)
            steps.append(step)
        return steps

    @staticmethod
    def _action(
        placement: Placement, found: Evaluation, *, force: bool, follow: bool
    ) -> tuple[Action | None, str]:
        state, detail = found.state, found.detail
        if found.error is not None:
            return "report", detail
        if state == "excluded":
            return "skip", f"{detail}; left as it is"
        if state == "off":
            return "report", detail
        if follow:
            action: Action | None = decide(placement.policy, state, moved=placement.moved)
            if action == "report" and state in ("modified", "conflict"):
                return action, f"{detail}; {_asked(placement, force=True)} replaces it (kept aside)"
            if action == "report":
                return action, f"{detail}; {placement.policy} waits: {_asked(placement)}"
            if action == "skip" and state != "applied":
                return action, f"{detail}; {placement.policy} leaves it"
        else:
            action = decide_asked(state, force=force)
            if action is None:
                return None, f"{detail}; pass --force to replace it (kept aside)"
            if action == "apply" and state in ("modified", "conflict"):
                return action, f"{detail}; the local version is kept aside"
        if action == "skip":
            return action, detail or "already applied"
        return action, detail

    def _previewed(self, step: Step) -> Step:
        """``step`` with what placing changes: a new file's size, or a diff of the content."""
        p = step.placement
        assert p.target is not None
        try:
            before = self._placer.read(p.target)
            data = p.source.tree.read(p.path)
            if p.placed_as == "merge":
                assert p.fmt is not None  # checked when the package loads
                after = self._placer.merged_text(data, p.target, fmt=p.fmt).encode("utf-8")
            else:
                after = data
        except UntapedError, OSError:  # the run itself says why
            return step
        state, record = step.found.state, step.found.record
        if state == "pending" and before is not None and before == after:  # already in place
            notes = [step.detail]
        else:
            lead = {"pending": "", "foreign": "", "new": ADDED}.get(state, step.detail)
            notes = [lead] if lead else []
            if p.placed_as == "merge" and before is not None:
                assert p.fmt is not None
                keys = sum(1 for _ in leaf_paths(self._placer.parse(data, fmt=p.fmt)))
                notes.append(f"merges {plural(keys, 'key')} into the existing file")
            elif before is None:
                notes.append(_size(after))
            elif record is None:
                notes.append("yours is kept aside first")
        diff = _diff(before, after, p.shown)
        if before is not None and before != after and diff is None:
            notes.append(
                f"binary, {plural(len(after), 'byte')}"
                if _binary(after)
                else "replaces a binary file"
            )
        if p.mode == "link" and p.placed_as == "copy" and record is None:
            notes.append(f"{p.policy} places a copy")
        return replace(step, detail="; ".join(notes), diff=diff)

    # -- executing -----------------------------------------------------------

    def planned(self, steps: Sequence[Step]) -> list[ApplyOutcome]:
        """The preview rows: what each step would do; a step that cannot is failed, as in a run."""
        rows: list[ApplyOutcome] = []
        for step in steps:
            action = self._planned_action(step)
            if step.converts and action in ("skipped", "unchanged"):
                action = "planned"
            error = step.found.error if action == "failed" else None
            rows.append(self._row(step, action, step.detail, error=error, diff=step.diff))
        return rows

    @staticmethod
    def waiting(steps: Sequence[Step]) -> int:
        """How many steps wait for a person: ``apply`` reports them, and exits 3."""
        return sum(1 for s in steps if s.action == "report" and s.found.error is None)

    def convert(self, steps: Sequence[Step]) -> dict[str, ApplyOutcome]:
        """Make each link whose item no longer follows ``sync`` a copy of what it shows now,
        before the clone moves, so nothing a program reads changes; the rows, by target."""
        rows: dict[str, ApplyOutcome] = {}
        for step in steps:
            if not step.converts:
                continue
            p, record = step.placement, step.found.record
            assert p.target is not None and record is not None
            try:
                data = self._inventory.link_destination(p.source, p.path).read_bytes()
                committed = self._evaluator.committed(p)
                self._placer.copy(data, p.target, executable=p.executable)
            except (UntapedError, OSError) as exc:
                rows[p.key] = self._failed(step, exc)
                continue
            source = data if committed is None else committed
            # untaped wrote the source: an edit the clone held reads as edited here
            self._store.put_placed(
                record.model_copy(
                    update={
                        "placed_as": "copy",
                        "source_hash": self._evaluator.source_hash(p, source),
                        "target_hash": content_hash(source),
                        "applied_at": self._now(),
                    }
                )
            )
            detail = f"became a copy of what it showed: {p.policy} places copies"
            if source != data:
                detail += "; it holds your edit, so it reads modified"
            rows[p.key] = self._row(step, "updated", detail, mode="copy")
        self._evaluator.reset()
        return rows

    def execute(
        self, steps: Sequence[Step], converted: dict[str, ApplyOutcome] | None = None
    ) -> list[ApplyOutcome]:
        """Run ``steps``; a path :meth:`convert` changed keeps that row unless placed again."""
        converted = converted or {}
        rows: list[ApplyOutcome] = []
        for step in steps:
            row = self._run(step)
            done = converted.get(step.placement.key)
            if done is not None:
                if row.action in ("created", "updated", "failed"):
                    detail = "; ".join(filter(None, ("became a copy", row.detail)))
                    row = row.model_copy(update={"detail": detail})
                elif step.action == "report" and done.action != "failed":
                    row = done.model_copy(update={"detail": f"{done.detail}; {row.detail}"})
                else:
                    row = done
            rows.append(row)
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
                gone = step.found.detail or "the source no longer has it"
                gone = f"taken back; {gone}"
                return self._row(
                    step, "deleted", f"{gone}; kept the edited version at {kept}" if kept else gone
                )
            kept = self._place(step)
            done: ApplyAction = "created" if step.found.record is None else "updated"
            detail = f"kept the previous version at {kept}" if kept else ""
            return self._row(step, done, detail, mode=step.placement.placed_as, diff=step.diff)
        except (UntapedError, OSError) as exc:
            return self._failed(step, exc)

    def _failed(self, step: Step, exc: Exception) -> ApplyOutcome:
        failure = exc if isinstance(exc, UntapedError) else DotfilesError(str(exc))
        return self._row(
            step,
            "failed",
            str(failure),
            error=Failure.from_exception(failure, message=str(failure)),
        )

    def _row(
        self,
        step: Step,
        action: ApplyAction,
        detail: str,
        *,
        error: Failure | None = None,
        mode: Mode | None = None,
        diff: str | None = None,
    ) -> ApplyOutcome:
        p = step.placement
        assert p.target is not None  # a placement waiting on an input is never planned
        return ApplyOutcome(
            source=p.source.name,
            package=p.package,
            item=p.item,
            entry=p.shown,
            mode=mode or self._evaluator.mode(p),
            action=action,
            state=step.found.state,
            detail=detail,
            diff=diff,
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
        mode = p.placed_as
        if mode == "merge":
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
            if mode == "link":
                destination = self._inventory.link_destination(p.source, p.path)
                target_hash = self._placer.link(destination, target)
                source_hash = target_hash
            else:
                data = p.source.tree.read(p.path)
                target_hash = self._placer.copy(data, target, executable=p.executable)
                source_hash = self._evaluator.source_hash(p, data)
        now = self._now()
        self._store.put_placed(
            PlacedRecord(
                target=p.key,
                source=p.source.name,
                package=p.package,
                item=p.item,
                path=p.path,
                entry=p.entry,
                declared=p.declared,
                placed_as=mode,
                fmt=p.fmt,
                source_commit=found.commit,
                source_hash=source_hash,
                target_hash=target_hash,
                managed=managed,
                applied_at=now,
            )
        )
        self._store.put_applied(owner, now)
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

    def removal(
        self,
        records: Sequence[PlacedRecord],
        *,
        keep_files: bool,
        clone: CloneRecord | None = None,
    ) -> list[ApplyOutcome]:
        """The preview of taking ``records`` back (or forgetting them, with ``keep_files``).

        ``clone``: the clone that goes with them, so a kept link into it becomes a copy.
        """
        return [
            self._removal_row(
                record, "planned", self._kept(record, clone) or "forgets it; the file stays"
            )
            if keep_files
            else self._removal_row(record, "planned", self._what(record))
            for record in records
        ]

    def take_back(
        self,
        records: Sequence[PlacedRecord],
        *,
        keep_files: bool,
        clone: CloneRecord | None = None,
    ) -> list[ApplyOutcome]:
        """Take every one of ``records`` back (or only forget it, with ``keep_files``)."""
        rows: list[ApplyOutcome] = []
        for record in records:
            if keep_files:
                try:
                    copied = self._unlink(record, clone)
                except OSError as exc:
                    broken = DotfilesError(str(exc))
                    rows.append(
                        self._removal_row(
                            record,
                            "failed",
                            str(broken),
                            error=Failure.from_exception(broken, message=str(broken)),
                        )
                    )
                    continue
                self._store.remove_placed(record.target)
                detail = (
                    "forgotten; the link became a copy" if copied else "forgotten; the file stays"
                )
                rows.append(self._removal_row(record, "deleted", detail))
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

    def _kept(self, record: PlacedRecord, clone: CloneRecord | None) -> str | None:
        """What ``--keep-files`` says of a link into ``clone``, which goes: it becomes a copy."""
        if self._into(record, clone) is None:
            return None
        return "forgets it; the link becomes a copy, since the clone goes"

    def _into(self, record: PlacedRecord, clone: CloneRecord | None) -> Path | None:
        """Where ``record``'s link points, when it still points into ``clone``; else ``None``."""
        if clone is None or record.placed_as != "link":
            return None
        target = Path(record.target)
        destination = Path(record.target_hash)
        if not target.is_symlink() or os.readlink(target) != record.target_hash:
            return None
        if Path(clone.folder) not in destination.parents or not destination.is_file():
            return None
        return destination

    def _unlink(self, record: PlacedRecord, clone: CloneRecord | None) -> bool:
        """Turn a link into ``clone`` into a copy of what it shows; whether it did."""
        destination = self._into(record, clone)
        if destination is None:
            return False
        executable = os.access(destination, os.X_OK)
        self._placer.copy(destination.read_bytes(), Path(record.target), executable=executable)
        return True

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


def _asked(placement: Placement, *, force: bool = False) -> str:
    """The ``apply`` that places ``placement`` whatever its policy."""
    from untaped_dotfiles.commands.apply import Apply  # noqa: PLC0415 - a cycle

    args = [placement.spelled, *(["--force"] if force else [])]
    return run_hint(Apply.spelled(*args))


def _size(data: bytes) -> str:
    """``new, N lines`` (or ``new, binary, N bytes``)."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return f"new, binary, {plural(len(data), 'byte')}"
    lines = text.count("\n") + (0 if not text or text.endswith("\n") else 1)
    return f"new, {plural(lines, 'line')}"


def _binary(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _diff(before: bytes | None, after: bytes, shown: str) -> str | None:
    """A unified diff from ``before`` (``None``: no file) to ``after``; ``None`` when nothing
    changes, or for binary content."""
    if before == after:
        return None
    try:
        old = before.decode("utf-8") if before is not None else None
        new = after.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return unified_diff_text(old, new, path=shown.removeprefix("/")) or None
