"""``untaped dotfiles apply``: place the files of the packages that are on.

It holds the plugin's lock, so two runs that change files never interleave,
and rewrites status.json and attention after it changed something.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

from pydantic import Field

from untaped.sdk import Confirm, Output, Positional, UntapedError, plural, q
from untaped_dotfiles.applier import Step
from untaped_dotfiles.commands._dotfiles import DotfilesCommand, Services
from untaped_dotfiles.inventory import Placement
from untaped_dotfiles.models import CloneRecord
from untaped_dotfiles.records import ApplyOutcome
from untaped_dotfiles.sources import current, resolve_url


class Apply(DotfilesCommand):
    """Place the files of the packages that are on (the named ones, or all), whatever their policy.

    A git source is cloned when it has no clone yet (a new machine, or a URL
    or ref changed in `config.yml`), fetched, and its clone moved to the
    fetched commit, so packages, links, copies and merges are what the source
    holds now; a failed fetch is reported and exits 1 (a --dry-run preview
    only warns, and reads a source with no clone from the fetch, cloning
    nothing). A clone with local changes (an edit through a link) is never
    moved: its source fails (exit 1), and the other sources apply. A local
    folder is read as it stands; a source that became a folder lets its old
    clone go once its links point at the folder. A file in the way of a new
    path is kept aside, and the row says where.
    Refuses paths edited on this machine (row action `conflict`, exit 1,
    under --dry-run too) unless --force. An entry whose target names an input
    with no value waits, naming the `config set` that answers it. The policy
    guide (linked from the dotfiles README) explains each state.
    """

    selectors: Positional[list[str]] = Field(
        default_factory=list,
        description="Packages or items (PKG, PKG/ITEM, SOURCE/PKG, SOURCE/PKG/ITEM). "
        "Default: every package that is on.",
    )
    force: bool = Field(
        False,
        description="Replace paths edited on this machine too (the local version is kept aside).",
    )
    confirm: Confirm
    output: Output[ApplyOutcome]

    def run(self) -> Iterator[ApplyOutcome]:
        svc = self.services()
        with svc.store.locked(), ExitStack() as peeks:
            if self.confirm.dry_run:
                self._peek_missing(svc, peeks)
            else:
                self._clone_missing(svc)
            fetched = self._fetch(svc)
            svc.catalog.forget()
            selections = (
                svc.catalog.selected(self.selectors) if self.selectors else svc.inventory.on()
            )
            self.report_sources(svc.catalog)
            resolved = svc.inventory.placements(selections)
            self.report(resolved.problems)
            placements = self._answered(resolved.placements)
            steps = svc.applier.plan_apply(placements, force=self.force)
            planned = svc.applier.planned(steps)
            todo = [s for s in steps if s.action in ("apply", "remove")]
            moving = [r for r in fetched if svc.clones.behind(r)]
            if not self.confirm(
                f"Apply {plural(len(todo), 'path')}?",
                preview=lambda: [planned],
                needed=bool(todo or moving),
            ):
                return iter(planned)
            if moving:
                steps = self._move(svc, moving, steps)
            rows = svc.applier.execute(steps)
            self._release_folders(svc)
            svc.refresh_status()
        self.output.empty("Nothing to apply.")
        self.report_rows(rows)
        return iter(rows)

    def _clone_missing(self, svc: Services) -> None:
        """Clone each git source that has no current clone; a failure is reported, not a stop."""
        for name in svc.sources.names:
            spec = svc.sources.spec(name)
            if spec.url is None or current(svc.store.get_clone(name), spec):
                continue
            try:
                record, _ = svc.clones.ensure(name, spec)
            except UntapedError as exc:
                self.ctx.diag.failure(exc, item=name)
                continue
            self.ctx.diag.info(f"cloned {q(name)} at {record.ref}")

    def _peek_missing(self, svc: Services, peeks: ExitStack) -> None:
        """Read each git source with no current clone from a fetch, for the preview only."""
        for name in svc.sources.names:
            spec = svc.sources.spec(name)
            if spec.url is None or current(svc.store.get_clone(name), spec):
                continue
            try:
                tree, branch, done = svc.clones.peek(resolve_url(spec.url), spec.ref)
            except UntapedError as exc:
                self.ctx.diag.warning(f"{name}: {exc}", hint=getattr(exc, "hint", None))
                continue
            peeks.callback(done)
            svc.sources.peek(name, tree)
            self.ctx.diag.info(f"{q(name)} has no clone yet: read at {branch}; apply clones it")

    def _release_folders(self, svc: Services) -> None:
        """Let go of the clone of each source that is a local folder now, once no link points
        into it."""
        placed = svc.store.placed()
        for record in svc.store.clones():
            spec = svc.sources.specs.get(record.source)
            if spec is None or spec.path is None:
                continue
            folder = Path(record.folder)
            if any(r.placed_as == "link" and folder in Path(r.target_hash).parents for r in placed):
                continue
            try:
                svc.clones.release(record)
            except UntapedError as exc:
                self.ctx.diag.failure(exc, item=record.source)
                continue
            svc.store.remove_clone(record.source)
            self.ctx.diag.info(f"{q(record.source)} is a local folder now: let its clone go")

    def _fetch(self, svc: Services) -> list[CloneRecord]:
        """Fetch the clone of every git source; the ones fetched, to move once confirmed."""
        fetched: list[CloneRecord] = []
        for name in svc.sources.names:
            spec = svc.sources.spec(name)
            record = svc.store.get_clone(name)
            if spec.url is None or not current(record, spec):
                continue
            assert record is not None
            try:
                svc.clones.fetch(record)
            except UntapedError as exc:
                if self.confirm.dry_run:
                    self.ctx.diag.warning(f"{name}: {exc}", hint=getattr(exc, "hint", None))
                else:
                    self.ctx.diag.failure(exc, item=name)
                continue
            fetched.append(record)
        return fetched

    def _answered(self, placements: Sequence[Placement]) -> list[Placement]:
        """The placements whose target is known; each one waiting on an input is said once."""
        for placement in placements:
            if placement.target is None and placement.excluded is None:
                self.ctx.diag.warning(
                    f"{placement.offer.spelled}/{placement.item} {placement.shown}: "
                    f"{placement.unanswered}"
                )
        return [p for p in placements if p.target is not None]

    def _move(self, svc: Services, moving: Sequence[CloneRecord], steps: list[Step]) -> list[Step]:
        """Move each clone to its fetched commit, then plan again.

        A clone that can't move (local changes) fails its source, and its
        entries are planned from the clone as it stands. A link the move
        already changed stays an ``apply`` step, so its record carries the new
        commit and its row says it changed.
        """
        for record in moving:
            try:
                svc.clones.move(record)
            except UntapedError as exc:
                self.ctx.diag.failure(exc, item=record.source)
        return _replanned(svc, steps, force=self.force)


def _replanned(svc: Services, steps: list[Step], *, force: bool) -> list[Step]:
    svc.evaluator.reset()
    applying = {s.placement.key for s in steps if s.action == "apply"}
    fresh = svc.applier.plan_apply([s.placement for s in steps], force=force)
    return [
        replace(s, action="apply", detail="moved with the clone")
        if s.action == "skip" and s.placement.key in applying
        else s
        for s in fresh
    ]
