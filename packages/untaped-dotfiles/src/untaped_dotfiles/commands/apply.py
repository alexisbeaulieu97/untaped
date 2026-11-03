"""``untaped dotfiles apply``: place the files of the packages that are on.

It holds the plugin's lock, so two runs that change files never interleave,
and rewrites status.json and attention after it changed something.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from pydantic import Field, JsonValue

from untaped.sdk import (
    Confirm,
    Failure,
    Matched,
    Output,
    Positional,
    PreviewItem,
    SettingsPlan,
    UntapedError,
    UsageError,
    key,
    plural,
    q,
    run_hint,
)
from untaped_dotfiles.applier import Step
from untaped_dotfiles.catalog import Offer, Selection
from untaped_dotfiles.commands._dotfiles import DotfilesCommand, Services
from untaped_dotfiles.inventory import Placement
from untaped_dotfiles.models import CloneRecord
from untaped_dotfiles.package import PackageInput
from untaped_dotfiles.records import ApplyOutcome
from untaped_dotfiles.settings import DotfilesSettings
from untaped_dotfiles.sources import current, resolve_url
from untaped_values.api import InputError, Vars, coerce, resolve, terminal_ask


class Apply(DotfilesCommand):
    """Place the files and run the steps of the packages that are on, by policy or as named.

    With no SELECTORS each entry follows its policy (the policy guide, linked
    from the dotfiles README, has the table): `sync` places what is new or
    changed and takes back what the source dropped, `once` places only what
    was never placed, `manual` places only what was just turned on, and the
    rest waits for a person. Naming packages or items, or `--all`, places
    them whatever their policy; a path edited here is refused (row action
    `conflict`, exit 1) unless --force, which keeps the local version aside.
    A `link` is placed as a link only under `sync`, else as a copy; a link
    whose item stops following `sync` becomes a copy of what it shows before
    the clone moves.

    A git source is cloned when it has no clone yet (a new machine, or a URL
    or ref changed in `config.yml`), fetched, and its clone moved to the
    fetched commit; a failed fetch is reported and exits 1 (a --dry-run
    preview only warns, and reads a source with no clone from the fetch,
    cloning nothing). A clone with local changes (an edit through a link) is
    never moved: its source's changes fail (exit 1) with a hint, and the
    other sources apply. A local folder is read as it stands; a source that
    became a folder lets its old clone go once its links point at the folder.

    The preview lists every entry with its destination, then a diff wherever
    existing content changes; a new file says `new, N lines` (its whole
    content is in the row's `diff` with `-f json`). An input a target needs
    and nothing answers is asked at a terminal before the preview, and kept
    in `config.yml` once confirmed; with no terminal, its entries wait (exit
    3) with the `add --var` that answers them. Exit 3 means done, with
    something left for a person: an entry the policy only reports, or one
    waiting on an input.

    Steps run after the files, only once confirmed: each check, then the
    step's program when it fails, then the check again (still failing:
    exit 1). A step follows only a policy this machine chose, else
    `manual`. Its own inputs are asked at a terminal each run (or given
    with --var) and never kept; a secret one reaches only the program's
    stdin. Then each working copy (`repos:`) whose target is absent is
    cloned; one already there is never moved.
    """

    selectors: Positional[list[str]] = Field(
        default_factory=list,
        description="Packages or items (PKG, PKG/ITEM, SOURCE/PKG, SOURCE/PKG/ITEM), placed "
        "whatever their policy. Default: every package that is on, by its policy.",
    )
    all_: bool = Field(False, description="Place every package that is on, whatever its policy.")
    force: bool = Field(
        False,
        description="Replace paths edited on this machine too (the local version is kept aside).",
    )
    vars: Vars = Field(default_factory=Vars)
    confirm: Confirm
    output: Output[ApplyOutcome]

    def run(self) -> Iterator[ApplyOutcome]:
        if self.all_ and self.selectors:
            raise UsageError("--all places every package that is on: name no SELECTORS with it")
        follow = not (self.selectors or self.all_)
        svc = self.services()
        with svc.store.locked(), ExitStack() as peeks:
            if self.confirm.dry_run:
                self._peek_missing(svc, peeks)
            else:
                self._clone_missing(svc)
            fetched = self._fetch(svc)
            svc.catalog.forget()
            selections = svc.catalog.selected(self.selectors) if self.selectors else None
            kept = self._inputs(svc, selections or svc.inventory.on())
            resolved = svc.inventory.placements(selections)
            self.report_sources(svc.catalog)
            self.report(resolved.problems)
            placements, unanswered = self._answered(resolved.placements)
            waiting = len(unanswered)
            stuck = self._stuck(svc, fetched)
            steps = _failing(
                svc.applier.plan_apply(placements, force=self.force, follow=follow), stuck
            )
            self._report_stuck(steps, stuck)
            listed = [e for e in svc.steps.entries(selections) if e.excluded is None]
            step_plans = svc.steps.failing(svc.steps.plan(listed, follow=follow), stuck)
            copies = [
                e
                for e in svc.repos.entries(selections, placements=placements)
                if e.excluded is None
            ]
            repo_plans = svc.repos.plan(copies)
            planned = [
                *svc.applier.planned(steps),
                *svc.steps.planned(step_plans),
                *svc.repos.planned(repo_plans),
            ]
            todo = [
                *(s for s in steps if s.action in ("apply", "remove") or s.converts),
                *(p for p in step_plans if p.action in ("apply", "remove")),
                *(p for p in repo_plans if p.action in ("apply", "remove")),
            ]
            moving = [r for r in fetched if r.source not in stuck and svc.clones.behind(r)]
            if todo:
                question = f"Apply {plural(len(todo), 'entry', 'entries')}?"
            elif moving:
                question = f"Move {plural(len(moving), 'clone')} to the fetched commit?"
            else:
                question = "Keep these answers in config.yml?"
            if not self.confirm(
                question,
                preview=lambda: _preview(planned, kept),
                needed=bool(todo or moving or kept is not None),
            ):
                rows = planned
                waiting += svc.applier.waiting(steps) + svc.steps.waiting(step_plans)
                waiting += svc.repos.waiting(repo_plans)
            else:
                waited = {  # items applied for the first time: their waiting entries were asked
                    owner: entries
                    for owner, entries in _by_item(unanswered).items()
                    if svc.store.applied_item(owner) is None
                }
                converted = svc.applier.convert(steps)
                if moving:
                    steps = self._move(svc, moving, steps, stuck, follow=follow)
                    step_plans = svc.steps.failing(step_plans, stuck)  # a clone that didn't move
                rows = svc.applier.execute(steps, converted)
                waiting += svc.applier.waiting(steps)
                ran, waits = svc.steps.execute(
                    step_plans,
                    given=self.vars.values(),
                    ask=terminal_ask(self.ctx.terminal, self.vars),
                    relay=self.ctx.diag.info,
                )
                cloned, copies_waiting = svc.repos.execute(repo_plans)
                rows = [*rows, *ran, *cloned]
                waiting += waits + copies_waiting
                for owner, entries in waited.items():
                    svc.store.note_waited(owner, entries)
                if kept is not None:
                    kept.write()
                self._release_folders(svc)
                svc.refresh_status()
                self._said(rows, waiting)
        self.output.empty("Nothing to apply.")
        self.report_rows(rows)
        yield from rows
        if waiting:
            raise Matched

    def _inputs(self, svc: Services, selections: Sequence[Selection]) -> SettingsPlan | None:
        """Answer the plain top-level inputs of the packages in this run, by one scope rule.

        A ``--var`` or ``--vars-file`` value goes to every package in the run
        that declares the name, and a name none declares exits 2. Each package
        uses ``--var``, then the files, then this machine's answer, then the
        default, then an answer at a terminal; what the flags or a prompt
        gave is kept in ``config.yml`` once confirmed (the returned plan).
        """
        named = _offers(selections)
        offers = [offer for offer in named if offer.on]
        given = self.vars.values()
        declared = {name for offer in offers for name in _declared(offer)}
        _refuse_unknown(set(given) - declared, declared, named)
        ask = terminal_ask(self.ctx.terminal, self.vars)
        changes: dict[Any, JsonValue] = {}
        for offer in offers:
            specs = _plain(offer)
            mine = _flagged(offer, given)
            answers = offer.choice.inputs if offer.choice is not None else {}
            config = {name: value for name, value in answers.items() if name in specs}
            try:
                found = resolve(specs, [mine, config], known=declared, ask=ask, label=offer.spelled)
            except InputError as exc:  # an answer that doesn't fit its input fails its package
                svc.inventory.fail(offer, exc)
                continue
            svc.inventory.answer(offer, found.values)
            for name in (*mine, *found.asked):
                value = cast("JsonValue", found.values[name])
                if config.get(name) != value:
                    setting = key(DotfilesSettings).sources[offer.source.name]
                    changes[setting.packages[offer.name].inputs[name]] = value
        return self.ctx.plugin.plan_settings(changes) if changes else None

    def _said(self, rows: Sequence[ApplyOutcome], waiting: int) -> None:
        """One line: how many entries changed, and how many need a person."""
        done = sum(1 for row in rows if row.action in ("created", "updated", "deleted"))
        if not done and not waiting:
            return
        needs = f"; {waiting} {'needs' if waiting == 1 else 'need'} you" if waiting else ""
        self.ctx.diag.success(f"applied {plural(done, 'entry', 'entries')}{needs}")

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
            here = Path(spec.path).expanduser().resolve()
            if here == folder.resolve() or folder.resolve() in here.parents:
                # the folder the source names now is the old clone: keep it, forget the record
                svc.store.remove_clone(record.source)
                self.ctx.diag.info(f"{q(record.source)} reads its old clone as a local folder now")
                continue
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

    def _stuck(self, svc: Services, fetched: Sequence[CloneRecord]) -> dict[str, UntapedError]:
        """Each fetched clone that should move but has local changes, by source, with why."""
        stuck: dict[str, UntapedError] = {}
        for record in fetched:
            why: UntapedError | None
            try:
                why = svc.clones.local_changes(record) if svc.clones.behind(record) else None
            except UntapedError as exc:
                why = exc
            if why is not None:
                stuck[record.source] = why
        return stuck

    def _report_stuck(self, steps: Sequence[Step], stuck: dict[str, UntapedError]) -> None:
        """Say why a stuck source stays put when none of its rows says it."""
        said = {s.placement.source.name for s in steps if s.found.error is not None}
        for name, exc in stuck.items():
            if name in said:
                continue
            if self.confirm.dry_run:
                self.ctx.diag.warning(f"{name}: {exc}", hint=getattr(exc, "hint", None))
            else:
                self.ctx.diag.failure(exc, item=name)

    def _answered(self, placements: Sequence[Placement]) -> tuple[list[Placement], list[Placement]]:
        """The placements whose target is known, and those waiting on an input (each said)."""
        waiting: list[Placement] = []
        for placement in placements:
            if placement.target is None and placement.excluded is None:
                waiting.append(placement)
                self.ctx.diag.warning(
                    f"{placement.spelled} {placement.shown}: {placement.unanswered}"
                )
        return [p for p in placements if p.target is not None], waiting

    def _move(
        self,
        svc: Services,
        moving: Sequence[CloneRecord],
        steps: list[Step],
        stuck: dict[str, UntapedError],
        *,
        follow: bool,
    ) -> list[Step]:
        """Move each clone to its fetched commit, then plan again.

        A clone that can't move fails its source's rows. A link the move
        already changed stays an ``apply`` step, so its record carries the new
        commit and its row says it changed.
        """
        failed: dict[str, UntapedError] = {}
        for record in moving:
            try:
                svc.clones.move(record)
            except UntapedError as exc:
                failed[record.source] = exc
        stuck.update(failed)
        fresh = _failing(_replanned(svc, steps, force=self.force, follow=follow), stuck)
        self._report_stuck(fresh, failed)
        return fresh


def _failing(steps: list[Step], stuck: dict[str, UntapedError]) -> list[Step]:
    """``steps`` with each change of a stuck source failed, carrying why (and its hint); a link
    to convert still converts, since the copy keeps what it shows."""
    if not stuck:
        return steps
    out: list[Step] = []
    for step in steps:
        exc = stuck.get(step.placement.source.name)
        if exc is not None and step.action in ("apply", "remove") and step.found.error is None:
            error = Failure.from_exception(exc, message=str(exc))
            step = replace(step, found=replace(step.found, error=error), detail=str(exc))
        out.append(step)
    return out


def _replanned(svc: Services, steps: list[Step], *, force: bool, follow: bool) -> list[Step]:
    svc.evaluator.reset()
    applying = {s.placement.key for s in steps if s.action == "apply"}
    fresh = svc.applier.plan_apply([s.placement for s in steps], force=force, follow=follow)
    diffs = {s.placement.key: s.diff for s in steps}
    return [
        replace(s, action="apply", detail="moved with the clone", diff=diffs.get(s.placement.key))
        if s.action == "skip" and s.placement.key in applying
        else s
        for s in fresh
    ]


def _offers(selections: Sequence[Selection]) -> list[Offer]:
    """The packages a run's selections name, each once, that load."""
    seen: dict[str, Offer] = {}
    for selection in selections:
        offer = selection.offer
        if offer.package is not None:
            seen.setdefault(offer.spelled, offer)
    return list(seen.values())


def _by_item(
    waiting: Sequence[Placement],
) -> dict[tuple[str, str, str], list[tuple[str, str]]]:
    """``(entry, declared)`` of each entry waiting on an input, by item: kept in its item's
    row, so the entry is ``pending`` once answered rather than ``new``."""
    found: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    for p in waiting:
        found.setdefault(p.owner, []).append((p.entry, p.declared))
    return found


def _refuse_unknown(unknown: set[str], declared: set[str], named: Sequence[Offer]) -> None:
    """Exit 2 for ``--var`` names no package of the run that is on declares, naming the
    packages that are off and declare them."""
    if not unknown:
        return
    names = ", ".join(sorted(unknown))
    off = sorted(o.spelled for o in named if not o.on and not unknown.isdisjoint(_declared(o)))
    if off:
        from untaped_dotfiles.commands.packages import Add  # noqa: PLC0415 - a cycle

        raise UsageError(
            f"{names}: declared only by packages that are off here ({', '.join(off)})",
            hint=f"turn them on first: {run_hint(Add.spelled(*off))}",
        )
    raise UsageError(
        f"no package in this run declares {names}; "
        f"declared: {', '.join(sorted(declared)) or '(none)'}"
    )


def _flagged(offer: Offer, given: dict[str, object]) -> dict[str, object]:
    """The ``--var``/``--vars-file`` values of ``offer``'s top-level inputs, coerced (a step's
    own inputs are checked too, and used when it runs); a secret, or a value that doesn't fit
    its input, exits 2."""
    assert offer.package is not None
    steps = _step_inputs(offer)
    mine: dict[str, object] = {}
    for name, value in given.items():
        spec = offer.package.inputs.get(name) or steps.get(name)
        if spec is None:
            continue
        if spec.secret:
            raise UsageError(
                f"input {q(name)} of {offer.spelled} is a secret: it is asked at a terminal, "
                "never given on the command line or kept in config.yml"
            )
        try:
            value = coerce(spec, value, name=name)
        except InputError as exc:
            raise UsageError(f"{offer.spelled}: {exc}") from exc
        if name in offer.package.inputs:
            mine[name] = value
    return mine


def _step_inputs(offer: Offer) -> dict[str, PackageInput]:
    """The inputs ``offer``'s steps declare: asked each time the step runs, never kept."""
    assert offer.package is not None
    return {
        name: spec
        for item in offer.package.items.values()
        for step in item.steps
        for name, spec in step.inputs.items()
    }


def _declared(offer: Offer) -> list[str]:
    assert offer.package is not None
    return [*offer.package.inputs, *_step_inputs(offer)]


def _plain(offer: Offer) -> dict[str, PackageInput]:
    """``offer``'s plain top-level inputs: a secret reaches only a step (and waits for one)."""
    assert offer.package is not None
    return {name: spec for name, spec in offer.package.inputs.items() if not spec.secret}


def _preview(rows: list[ApplyOutcome], kept: SettingsPlan | None) -> list[PreviewItem]:
    """The rows, then a diff wherever existing content changes, then the answers kept."""
    items: list[PreviewItem] = [rows]
    for row in rows:
        if row.action == "planned" and row.diff and not row.diff.startswith("--- /dev/null"):
            items.append(f"# {row.entry}")
            items.append(row.diff.removesuffix("\n"))
    if kept is not None:
        items.extend(kept.preview())
    return items
