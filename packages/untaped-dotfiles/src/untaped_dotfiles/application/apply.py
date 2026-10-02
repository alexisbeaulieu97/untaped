"""``apply`` and ``sync``: pull what may be pulled, then place, skip or report each path.

``sync`` fast-forwards a managed clone only when every enabled ``link``
file reading from it has policy ``sync`` and the working tree is clean: one
``manual`` link file holds the whole clone back, and ``sync`` reports those
files as ``behind`` so the hold is never silent. ``apply ITEM`` pulls the
clone when the item has a link file in it, which moves every other link
file on that clone too; the plan lists them before the confirmation.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import ErrorInfo, UntapedError, note_failure, q
from untaped_dotfiles.domain.models import AppliedRecord, RepoRecord
from untaped_dotfiles.domain.records import PlaceAction, PlaceOutcome, RepoOutcome
from untaped_dotfiles.domain.status import Action, sync_action, target_modified
from untaped_dotfiles.errors import DotfilesError, GitError

if TYPE_CHECKING:
    from untaped_dotfiles.application.inventory import Inventory, Placement
    from untaped_dotfiles.application.ports import DotfilesStore, GitRepos, Placer
    from untaped_dotfiles.application.status import Evaluation, Evaluator


@dataclass(frozen=True)
class Step:
    """What will happen to one placement."""

    placement: Placement
    found: Evaluation
    action: Action | None
    """``None``: refused (a ``modified`` or ``conflict`` path without ``--force``)."""
    detail: str = ""


class Applier:
    """Plans and runs placements for ``apply`` and ``sync``."""

    def __init__(
        self,
        store: DotfilesStore,
        git: GitRepos,
        placer: Placer,
        inventory: Inventory,
        evaluator: Evaluator,
        *,
        now: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._git = git
        self._placer = placer
        self._inventory = inventory
        self._evaluator = evaluator
        self._now = now

    # -- pulling -------------------------------------------------------------

    def fetch_all(self, repos: Sequence[RepoRecord]) -> list[RepoOutcome]:
        """Fetch every repo; a failure is a row, not a stop."""
        rows: list[RepoOutcome] = []
        for repo in repos:
            try:
                self._git.fetch(Path(repo.path))
            except UntapedError as exc:
                rows.append(
                    RepoOutcome(
                        name=repo.name,
                        action="failed",
                        detail=f"fetch failed: {exc}",
                        error=note_failure(exc, message=str(exc)),
                    )
                )
        return rows

    def held_back_by(self, repo: RepoRecord, placements: Sequence[Placement]) -> list[str]:
        """The ``manual`` link files (``item/file``) that keep ``repo``'s clone where it is."""
        return sorted(
            {
                f"{p.item}/{p.file}"
                for p in placements
                if p.repo.name == repo.name
                and p.mode == "link"
                and p.choice.policy == "manual"
                and not p.excluded
            }
        )

    def fast_forward(self, repo: RepoRecord, *, held: Sequence[str] = ()) -> RepoOutcome:
        """Fast-forward a managed clone unless held back or dirty.

        A registered checkout is only fetched.
        """
        if not repo.managed:
            return RepoOutcome(name=repo.name, action="unchanged", detail="fetched; not pulled")
        if held:
            return RepoOutcome(
                name=repo.name,
                action="skipped",
                detail=f"held back by manual link files: {', '.join(held)}",
            )
        path = Path(repo.path)
        try:
            if not self._git.is_clean(path):
                raise GitError(
                    f"the clone of {q(repo.name)} has local changes; not fast-forwarded",
                    category="conflict",
                    hint=f"commit or discard the changes in {path}",
                )
            moved = self._git.fast_forward(path, repo.ref)
        except UntapedError as exc:
            return RepoOutcome(
                name=repo.name,
                action="failed",
                detail=str(exc),
                error=note_failure(exc, message=str(exc)),
            )
        self._evaluator.reset()
        if moved:
            return RepoOutcome(name=repo.name, action="updated", detail="fast-forwarded")
        return RepoOutcome(name=repo.name, action="unchanged", detail="up to date")

    # -- planning ------------------------------------------------------------

    def plan_apply(self, placements: Sequence[Placement], *, force: bool) -> list[Step]:
        """Explicit ``apply``: every path, whatever its policy; local edits need ``--force``."""
        self._evaluator.prime_changes(placements)
        steps: list[Step] = []
        for placement in placements:
            found = self._evaluator.evaluate(placement)
            steps.append(Step(placement, found, *self._apply_action(found, force=force)))
        return steps

    def replan(self, steps: Sequence[Step], *, force: bool) -> list[Step]:
        """Plan again after a pull.

        A link the pull already moved stays an ``apply`` step, so the record
        carries the new commit and the row says the path changed.
        """
        moved = {s.placement.key for s in steps if s.action == "apply"}
        fresh = self.plan_apply([s.placement for s in steps], force=force)
        return [
            replace(s, action="apply", detail="moved with the clone")
            if s.action == "skip" and s.placement.key in moved
            else s
            for s in fresh
        ]

    def plan_sync(self, placements: Sequence[Placement]) -> list[Step]:
        """``sync``: the policy table decides."""
        self._evaluator.prime_changes(placements)
        steps: list[Step] = []
        for placement in placements:
            found = self._evaluator.evaluate(placement)
            if found.error is not None:
                steps.append(Step(placement, found, "report", found.detail))
                continue
            action = sync_action(placement.choice.policy, found.state)
            detail = self._report_detail(placement, found) if action == "report" else found.detail
            steps.append(Step(placement, found, action, detail))
        return steps

    def _apply_action(self, found: Evaluation, *, force: bool) -> tuple[Action | None, str]:
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

    @staticmethod
    def _report_detail(placement: Placement, found: Evaluation) -> str:
        state = found.state
        if state in ("behind", "pending", "foreign"):
            lead = found.detail or f"{state}"
            return f"{lead}; run `untaped dotfiles apply {placement.item}`"
        return found.detail

    # -- executing -----------------------------------------------------------

    def planned(self, steps: Sequence[Step]) -> list[PlaceOutcome]:
        """The dry-run rows: what each step would do."""
        return [self._row(step, self._planned_action(step), step.detail) for step in steps]

    def execute(self, steps: Sequence[Step]) -> list[PlaceOutcome]:
        rows: list[PlaceOutcome] = []
        for step in steps:
            rows.append(self._run(step))
        self._evaluator.reset()
        return rows

    def _planned_action(self, step: Step) -> PlaceAction:
        if step.action is None:
            return "conflict"
        if step.found.error is not None:
            return "failed"
        if step.action in ("apply", "remove"):
            return "planned"
        return "unchanged" if step.found.state == "applied" else "skipped"

    def _run(self, step: Step) -> PlaceOutcome:
        action = self._planned_action(step)
        if action == "failed":
            return self._row(step, "failed", step.detail, error=step.found.error)
        if action != "planned":
            return self._row(step, action, step.detail)
        try:
            if step.action == "remove":
                kept = self._remove(step)
                gone = "removed; the source is gone from the repo"
                return self._row(
                    step, "deleted", f"{gone}; kept the edited version at {kept}" if kept else gone
                )
            kept = self._place(step)
            done: PlaceAction = "created" if step.found.record is None else "updated"
            detail = f"kept the previous version at {kept}" if kept else ""
            return self._row(step, done, detail)
        except (UntapedError, OSError) as exc:
            failure = exc if isinstance(exc, UntapedError) else DotfilesError(str(exc))
            return self._row(
                step, "failed", str(failure), error=note_failure(failure, message=str(failure))
            )

    def _row(
        self, step: Step, action: PlaceAction, detail: str, *, error: ErrorInfo | None = None
    ) -> PlaceOutcome:
        p = step.placement
        return PlaceOutcome(
            repo=p.repo.name,
            item=p.item,
            source=p.source,
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
        record = found.record
        target = p.target
        ours = record is not None and found.state in ("behind", "applied", "missing")
        present = target.is_symlink() or target.exists()
        kept: Path | None = None
        if p.mode == "merge":
            if present and not ours:
                kept = self._placer.keep_aside(target, repo=p.repo.name, item=p.item, copy=True)
            data = self._evaluator.tree(p.repo).read(p.source)
            assert p.fmt is not None  # validated by the manifest
            target_hash, managed = self._placer.merge(data, target, fmt=p.fmt)
            source_hash = self._evaluator.source_hash(p, data)
        else:
            if present and not ours:
                kept = self._placer.keep_aside(target, repo=p.repo.name, item=p.item)
            managed = ()
            if p.mode == "link":
                destination = self._inventory.link_destination(p.repo, p.source)
                target_hash = self._placer.link(destination, target)
                source_hash = target_hash
            else:
                data = self._evaluator.tree(p.repo).read(p.source)
                target_hash = self._placer.copy(data, target, executable=p.executable)
                source_hash = self._evaluator.source_hash(p, data)
        self._store.put_applied(
            AppliedRecord(
                target=p.key,
                repo=p.repo.name,
                item=p.item,
                file=p.file,
                source=p.source,
                mode=p.mode,
                fmt=p.fmt,
                source_commit=found.commit,
                source_hash=source_hash,
                target_hash=target_hash,
                managed=managed,
                applied_at=self._now(),
            )
        )
        return kept

    def _remove(self, step: Step) -> Path | None:
        """Remove what the tool placed.

        A target that is no longer what the tool wrote (whatever its state,
        an orphan included) is kept aside instead of deleted; a merge target
        whose managed keys were edited is copied aside before the keys go.
        """
        p, record = step.placement, step.found.record
        if record is None:
            return None
        target = p.target
        kept: Path | None = None
        present = target.is_symlink() or target.exists()
        if p.mode == "merge":
            if present and p.fmt is not None:
                observed = self._placer.observe(target, fmt=p.fmt, managed=record.managed)
                if target_modified(p.mode, record, observed):
                    kept = self._placer.keep_aside(target, repo=p.repo.name, item=p.item, copy=True)
                self._placer.unmerge(target, fmt=p.fmt, managed=record.managed)
        elif present:
            if target_modified(p.mode, record, self._placer.observe(target)):
                kept = self._placer.keep_aside(target, repo=p.repo.name, item=p.item)
            else:
                self._placer.delete(target)
        self._store.remove_applied(record.target)
        return kept

    def remove_all(self, steps: Sequence[Step]) -> list[PlaceOutcome]:
        """``remove``: delete every recorded placement among ``steps``."""
        rows: list[PlaceOutcome] = []
        for step in steps:
            if step.found.record is None:
                rows.append(self._row(step, "skipped", "never placed by this tool"))
                continue
            try:
                kept = self._remove(step)
            except (UntapedError, OSError) as exc:
                failure = exc if isinstance(exc, UntapedError) else DotfilesError(str(exc))
                rows.append(
                    self._row(
                        step,
                        "failed",
                        str(failure),
                        error=note_failure(failure, message=str(failure)),
                    )
                )
                continue
            detail = f"kept the edited version at {kept}" if kept else ""
            rows.append(self._row(step, "deleted", detail))
        self._evaluator.reset()
        return rows

    def preview_removal(self, steps: Sequence[Step]) -> list[PlaceOutcome]:
        return [
            self._row(step, "planned" if step.found.record is not None else "skipped", step.detail)
            for step in steps
        ]
