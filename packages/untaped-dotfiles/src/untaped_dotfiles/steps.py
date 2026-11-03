"""Steps: a check plus what fixes it. Their entries, states, plans and runs.

A step follows only a policy this machine chose (on its item or package),
else ``manual``, whatever the author suggests: a source can't make its later
step changes run on a subscriber's timer. ``pending``, ``new`` and ``behind``
are known without running anything; ``applied``, ``drifted`` and ``unmet``
come from running the check as last applied (:mod:`~untaped_dotfiles.step_runner`
says what may run).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue

from untaped.sdk import Failure, UntapedError, q, run_hint
from untaped_dotfiles.errors import DotfilesError
from untaped_dotfiles.hashing import content_hash
from untaped_dotfiles.inventory import unanswered
from untaped_dotfiles.models import StepRecord
from untaped_dotfiles.policy import Policy
from untaped_dotfiles.records import ApplyAction, ApplyOutcome, EntryStatus
from untaped_dotfiles.states import Action, StepState, decide_step, decide_step_asked
from untaped_dotfiles.step_runner import ApprovedStep, Checked, StepRunner, step_digest
from untaped_values.api import Ask, placeholders, resolve

if TYPE_CHECKING:
    from untaped_dotfiles.catalog import Offer, Selection
    from untaped_dotfiles.inventory import Inventory
    from untaped_dotfiles.package import PackageInput, Step
    from untaped_dotfiles.ports import DotfilesStore

#: The detail of a ``new`` step.
ADDED = "added to the package since the item was applied here"
#: The detail of a step that is forgotten, planned and done.
FORGETS = "forgets it; nothing it did is undone"
FORGOTTEN = "forgotten; nothing it did is undone"


@dataclass(frozen=True)
class StepEntry:
    """One step of an item that is on, or a ``steps`` row its package no longer runs."""

    offer: Offer
    item: str
    step: Step
    policy: Policy
    """This machine's choice, else ``manual``."""
    chosen: bool
    record: StepRecord | None = None
    excluded: str | None = None
    off: bool = False
    orphan: bool = False

    @property
    def key(self) -> str:
        return f"{self.offer.spelled}/{self.item}/{self.step.name}"

    @property
    def owner(self) -> tuple[str, str, str]:
        return (self.offer.source.name, self.offer.name, self.item)

    @property
    def spelled(self) -> str:
        """``SOURCE/PKG/ITEM``."""
        return f"{self.offer.spelled}/{self.item}"

    @property
    def specs(self) -> dict[str, PackageInput]:
        """Every input the step may name: its package's, then its own."""
        package = self.offer.package
        top = dict(package.inputs) if package is not None else {}
        return {**top, **self.step.inputs}


@dataclass(frozen=True)
class StepFound:
    """A step's state and why."""

    state: StepState
    detail: str = ""
    error: Failure | None = None


@dataclass(frozen=True)
class StepPlan:
    """What ``apply`` does with one step."""

    entry: StepEntry
    found: StepFound
    action: Action
    detail: str
    script_hash: str | None = None
    """The hash of the step's script as planned: what confirming the plan approves."""


#: The ``declared`` half of a step's ``ItemRecord.waited`` pair (a target never reads so).
WAITED = "step"


class Steps:
    """Lists, evaluates, plans and runs the steps of the packages that are on."""

    def __init__(
        self,
        inventory: Inventory,
        store: DotfilesStore,
        runner: StepRunner,
        *,
        now: Callable[[], datetime],
    ) -> None:
        self._inventory = inventory
        self._store = store
        self._runner = runner
        self._now = now
        self._checked: dict[str, Checked] = {}
        """The last check of each step in this run, so ``status`` after ``apply`` reruns none."""

    # -- entries -------------------------------------------------------------

    def entries(self, selections: Sequence[Selection] | None = None) -> list[StepEntry]:
        """The steps of the items that are on in ``selections`` (every package that is on when
        ``None``), plus the ``steps`` rows they no longer run (``orphan``) or whose package or
        item is off now (``off``)."""
        found: list[StepEntry] = []
        machine = self._inventory.machine
        for offer, item in self._inventory.wanted(selections):
            package = offer.package
            if package is None or offer.gone or self._inventory.broken(offer) is not None:
                continue
            policy, chosen = _policy(offer, item)
            reason = offer.exclusion(machine, item)
            for step in package.items[item].steps:
                excluded = reason or step.exclusion(machine.os, machine.tags)
                found.append(StepEntry(offer, item, step, policy, chosen, excluded=excluded))
        records = {r.step: r for r in self._store.steps()}
        found = [replace(e, record=records.get(e.key)) for e in found]
        listed = {e.key for e in found}
        wanted = {(o.source.name, o.name, i): o for o, i in self._inventory.wanted(selections)}
        off = self._inventory.off_scope(selections)
        for record in records.values():
            if record.step not in listed:
                found.extend(self._unlisted(record, wanted, off))
        return found

    def _unlisted(
        self,
        record: StepRecord,
        wanted: Mapping[tuple[str, str, str], Offer],
        off: Mapping[tuple[str, str, str], Offer],
    ) -> list[StepEntry]:
        """A ``steps`` row no listed step matches: ``orphan`` (its item or step is gone),
        ``off`` (its package or item is off now), or nothing (out of scope, or unreadable)."""
        source, package, item, _ = record.step.split("/", 3)
        owned = wanted.get((source, package, item))
        if owned is not None:
            readable = owned.package is not None and not owned.gone
            if readable and self._inventory.broken(owned) is None:
                return [self._from_record(record, owned, item, orphan=True)]
            return []
        scope = off.get((source, package, item)) or off.get((source, package, ""))
        if scope is None:
            return []
        if not scope.item_on(item):
            return [self._from_record(record, scope, item, off=True)]
        if scope.package is None or item not in scope.package.items:
            return [self._from_record(record, scope, item, orphan=True)]
        return []

    @staticmethod
    def _from_record(
        record: StepRecord, offer: Offer, item: str, *, orphan: bool = False, off: bool = False
    ) -> StepEntry:
        policy, chosen = _policy(offer, item)
        return StepEntry(
            offer, item, record.declared, policy, chosen, record=record, orphan=orphan, off=off
        )

    # -- states --------------------------------------------------------------

    def evaluate(self, entry: StepEntry) -> StepFound:
        """``entry``'s state; runs only the check as last applied, for an unchanged step."""
        if entry.excluded is not None:
            return StepFound("excluded", entry.excluded)
        if entry.off:
            return StepFound("off", _off_detail(entry))
        if entry.orphan:
            return StepFound("orphan", "the source no longer has it")
        waits = self.unanswered(entry)
        if waits:  # nothing can run, nor its stored check, until the input is answered
            return StepFound("pending" if entry.record is None else "behind", waits)
        return self._stored(entry)

    def unanswered(self, entry: StepEntry) -> str:
        """Why ``entry`` waits on a top-level input with no value here, or ``""``."""
        if entry.excluded is not None or entry.off or entry.orphan:
            return ""
        package = entry.offer.package
        assert package is not None
        values = self._inventory.values(entry.offer)
        missing = [
            name
            for name in self._names(entry)
            if name in package.inputs and not package.inputs[name].secret and name not in values
        ]
        return unanswered(entry.offer, missing) if missing else ""

    @staticmethod
    def _names(entry: StepEntry) -> list[str]:
        """The inputs the step uses, in order, each once: those its text fields name, then
        the one its stdin reads."""
        step = entry.step
        found = [name for at, text in step.texts() for name in placeholders(text, field=at)]
        return list(dict.fromkeys([*found, *([step.stdin] if step.stdin else [])]))

    def _stored(self, entry: StepEntry) -> StepFound:
        """The state of a step of an item that is on, from its row and its stored check."""
        step = entry.step
        if step.kind is not None:
            return StepFound("unmet", f"needs a plugin that provides step kind {q(step.kind)}")
        try:
            digest = step_digest(step, self._script_hash(entry))
        except UntapedError as exc:
            return _error(exc)
        record = entry.record
        if record is None:
            item = self._store.applied_item(entry.owner)
            if item is not None and (step.name, WAITED) not in item.waited:
                return StepFound("new", ADDED)
            return StepFound("pending")
        if record.digest != digest:
            return StepFound("behind", "changed in the source since it was applied")
        if record.values != self._values(entry):
            return StepFound("pending", "an input it uses changed")
        checked = self._checked.get(entry.key)
        if checked is None:
            try:
                checked = self._runner.check(
                    ApprovedStep.from_record(record, specs=entry.specs, script=self._script(entry))
                )
            except UntapedError as exc:
                return _error(exc)
            self._checked[entry.key] = checked
        if checked.passed:
            return StepFound("applied")
        if _report_only(step):
            return StepFound("unmet", f"needs you: {step.fix}")
        return StepFound("drifted", f"its check fails now: {checked.detail}")

    def _script(self, entry: StepEntry) -> Path | None:
        script = entry.step.script
        return entry.offer.source.root / script if script is not None else None

    def _script_hash(self, entry: StepEntry) -> str | None:
        script = entry.step.script
        if script is None:
            return None
        tree = entry.offer.source.tree
        if not tree.exists(script) or tree.is_dir(script):
            raise DotfilesError(
                f"step {q(entry.step.name)}: script {q(script)} is not in the source"
            )
        return content_hash(tree.read(script))

    def _values(self, entry: StepEntry) -> dict[str, JsonValue]:
        """The plain top-level values the step's text fields use, as they are now."""
        package = entry.offer.package
        assert package is not None
        values = self._inventory.values(entry.offer)
        return {
            name: cast("JsonValue", values[name])
            for name in sorted(self._names(entry))
            if name in package.inputs and name in values
        }

    # -- status --------------------------------------------------------------

    def status_rows(self, entries: Sequence[StepEntry]) -> tuple[list[EntryStatus], list[Action]]:
        rows: list[EntryStatus] = []
        actions: list[Action] = []
        for entry in entries:
            found = self.evaluate(entry)
            rows.append(
                EntryStatus(
                    source=entry.offer.source.name,
                    package=entry.offer.name,
                    item=entry.item,
                    kind="step",
                    entry=entry.step.name,
                    policy=entry.policy,
                    chosen=entry.chosen,
                    state=found.state,
                    detail=found.detail,
                    error=found.error,
                )
            )
            waits = found.error is not None or bool(self.unanswered(entry))
            actions.append("report" if waits else decide_step(entry.policy, found.state))
        return rows, actions

    # -- planning ------------------------------------------------------------

    def plan(self, entries: Sequence[StepEntry], *, follow: bool) -> list[StepPlan]:
        """What ``apply`` does with each step: by its policy (``follow``), else as asked."""
        plans: list[StepPlan] = []
        for entry in entries:
            found = self.evaluate(entry)
            if found.error is not None or self.unanswered(entry):
                action: Action = "report"
            elif follow:
                action = decide_step(entry.policy, found.state)
            else:
                action = decide_step_asked(found.state)
            detail = _planned_detail(entry, found, action)
            plans.append(StepPlan(entry, found, action, detail, self._planned_hash(entry)))
        return plans

    def _planned_hash(self, entry: StepEntry) -> str | None:
        try:
            return self._script_hash(entry)
        except UntapedError:  # the plan's row already says why
            return None

    @staticmethod
    def failing(plans: Sequence[StepPlan], stuck: Mapping[str, UntapedError]) -> list[StepPlan]:
        """``plans`` with each step to run or forget of a stuck source failed, carrying why:
        what its clone holds is not what the plan shows."""
        out: list[StepPlan] = []
        for plan in plans:
            exc = stuck.get(plan.entry.offer.source.name)
            if exc is not None and plan.action in ("apply", "remove") and plan.found.error is None:
                failure = Failure.from_exception(exc, message=str(exc))
                plan = replace(plan, found=replace(plan.found, error=failure), detail=str(exc))
            out.append(plan)
        return out

    @staticmethod
    def planned(plans: Sequence[StepPlan]) -> list[ApplyOutcome]:
        return [_row(p, _planned_action(p), p.detail, error=p.found.error) for p in plans]

    @staticmethod
    def waiting(plans: Sequence[StepPlan]) -> int:
        """How many steps wait for a person: ``apply`` exits 3 for them."""
        return sum(1 for p in plans if p.action == "report" and p.found.error is None)

    def forgetting(
        self, prefixes: Sequence[tuple[str, ...]], *, done: bool = False
    ) -> list[ApplyOutcome]:
        """A row for each step applied under ``prefixes`` (sources, packages or items) that
        turning them off forgets; ``done``: they were. Nothing a step did is undone."""
        starts = ["/".join(prefix) for prefix in prefixes]
        rows: list[ApplyOutcome] = []
        for record in self._store.steps():
            if not any(record.step == s or record.step.startswith(s + "/") for s in starts):
                continue
            source, package, item, name = record.step.split("/", 3)
            rows.append(
                ApplyOutcome(
                    source=source,
                    package=package,
                    item=item,
                    kind="step",
                    entry=name,
                    action="deleted" if done else "planned",
                    detail=FORGOTTEN if done else FORGETS,
                )
            )
        return rows

    # -- running -------------------------------------------------------------

    def execute(
        self,
        plans: Sequence[StepPlan],
        *,
        given: Mapping[str, object],
        ask: Ask | None,
        relay: Callable[[str], None],
    ) -> tuple[list[ApplyOutcome], int]:
        """Run every planned step in order, after the confirmation; the rows, and how many
        steps wait for a person (a report, or an input with no terminal to ask)."""
        runner = self._runner.with_relay(relay)
        self._checked.clear()  # planned before files were placed: what the status reads reruns
        rows: list[ApplyOutcome] = []
        waiting = 0
        for plan in plans:
            if plan.found.error is not None:  # unreadable, or its source's clone is stuck
                rows.append(_row(plan, "failed", plan.detail, error=plan.found.error))
            elif plan.action == "remove":
                self._store.forget_steps((*plan.entry.owner, plan.entry.step.name))
                rows.append(_row(plan, "deleted", FORGOTTEN))
            elif plan.action != "apply":
                waiting += plan.action == "report"
                rows.append(_row(plan, _kept(plan), plan.detail))
            else:
                row, waits = self._apply(plan, runner, given=given, ask=ask)
                rows.append(row)
                waiting += waits
        return rows, waiting

    def _apply(
        self, plan: StepPlan, runner: StepRunner, *, given: Mapping[str, object], ask: Ask | None
    ) -> tuple[ApplyOutcome, bool]:
        """Check the step, run it when the check fails (asking its inputs only then), check
        again; the row, and whether it waits for a person. A step that doesn't end applied is
        noted on its item, so it stays ``pending`` rather than turning ``new``."""
        row, waits = self._tried(plan, runner, given=given, ask=ask)
        if row.action in ("failed", "skipped") and not (waits and _report_only(plan.entry.step)):
            self._store.note_waited(plan.entry.owner, [(plan.entry.step.name, WAITED)])
        return row, waits

    def _tried(
        self, plan: StepPlan, runner: StepRunner, *, given: Mapping[str, object], ask: Ask | None
    ) -> tuple[ApplyOutcome, bool]:
        entry, step = plan.entry, plan.entry.step
        script = self._script(entry)
        values = self._values(entry)
        try:
            self._same_script(plan, script)
            checking = ApprovedStep.from_plan(
                entry.key, step, confirmed=True, specs=entry.specs, values=values, script=script
            )
            checked = self._checked[entry.key] = runner.check(checking)
        except UntapedError as exc:
            return _failed(plan, exc), False
        if checked.passed:
            self._store_step(plan)
            return _row(plan, "unchanged", "the check passes; nothing ran"), False
        if _report_only(step):
            self._store_step(plan)
            return _row(plan, "skipped", f"needs you: {step.fix}"), True
        asked = dict(step.inputs)
        if step.stdin is not None and entry.specs[step.stdin].secret:
            asked[step.stdin] = entry.specs[step.stdin]  # a secret is never kept: asked each run
        mine = {name: value for name, value in given.items() if name in asked}
        try:
            found = resolve(asked, [mine], ask=ask, label=f"{entry.spelled} {step.name}")
        except UntapedError as exc:
            return _failed(plan, exc), False
        if found.missing:
            return _row(plan, "skipped", _missing(found.missing, asked)), True
        everything = {**values, **found.values}
        secrets = [str(found.values[n]) for n in asked if asked[n].secret and n in found.values]
        stdin = None
        if step.stdin is not None:
            value = everything.get(step.stdin)
            stdin = str(value if value is not None else "").encode("utf-8")
        approved = ApprovedStep.from_plan(
            entry.key,
            step,
            confirmed=True,
            specs=entry.specs,
            values={k: v for k, v in everything.items() if not entry.specs[k].secret},
            script=script,
        )
        try:
            runner.run(approved, stdin=stdin, secrets=secrets)
            checked = self._checked[entry.key] = runner.check(approved)
        except UntapedError as exc:
            return _failed(plan, exc), False
        if not checked.passed:
            failure = DotfilesError(f"ran, but the check still fails: {checked.detail}")
            return _failed(plan, failure), False
        self._store_step(plan)
        done: ApplyAction = "created" if entry.record is None else "updated"
        return _row(plan, done, "ran; the check passes now"), False

    @staticmethod
    def _same_script(plan: StepPlan, script: Path | None) -> None:
        """Refuse a script whose file in the clone isn't the one the plan showed."""
        if script is None:
            return
        try:
            data = script.read_bytes()
        except OSError as exc:
            raise DotfilesError(f"cannot read the script {script}: {exc.strerror or exc}") from exc
        if content_hash(data) != plan.script_hash:
            raise DotfilesError(
                f"the script {script} isn't the one the preview showed; nothing ran",
                hint="run `untaped dotfiles apply` again once the source's clone is clean",
            )

    def _store_step(self, plan: StepPlan) -> None:
        """Store the step as the plan showed it: the confirmation approved that."""
        entry, script_hash = plan.entry, plan.script_hash
        now = self._now()
        self._store.put_step(
            StepRecord(
                step=entry.key,
                declared=entry.step,
                script_hash=script_hash,
                digest=step_digest(entry.step, script_hash),
                values=self._values(entry),
                applied_at=now,
                checked_at=now,
            )
        )
        self._store.put_applied(entry.owner, now)


def _policy(offer: Offer, item: str) -> tuple[Policy, bool]:
    """A step's policy: this machine's choice, else ``manual``."""
    policy, chosen = offer.item_policy(item)
    return (policy, True) if chosen and policy is not None else (Policy.MANUAL, False)


def _missing(names: Sequence[str], asked: Mapping[str, PackageInput]) -> str:
    plain = [name for name in names if not asked[name].secret]
    hint = (
        "; " + run_hint("dotfiles apply " + " ".join(f"--var {n}=VALUE" for n in plain))
        if plain
        else ""
    )
    return f"needs input {', '.join(q(name) for name in names)}: no terminal to ask{hint}"


def _report_only(step: Step) -> bool:
    return step.run is None and step.script is None


def _error(exc: UntapedError) -> StepFound:
    return StepFound("error", str(exc), Failure.from_exception(exc, message=str(exc)))


def _off_detail(entry: StepEntry) -> str:
    from untaped_dotfiles.commands.packages import Remove  # noqa: PLC0415 - a cycle

    offer = entry.offer
    which = "package" if not offer.on else "item"
    spelled = offer.spelled if not offer.on else entry.spelled
    forget = run_hint(Remove.spelled(spelled))
    return f"applied earlier; the {which} is off now; to forget it, {forget}"


def describe(step: Step) -> str:
    """What applying ``step`` does, as the package writes it: ``if CHECK fails, runs: ARGV``."""
    check = step.check
    if check is None:
        when = "always"
    elif check.path is not None:
        when = f"if {check.path} is absent"
    elif check.program is not None:
        when = f"if `{check.program}` is not on PATH"
    else:
        command = " ".join(check.command or ())
        when = (
            f"if `{command}` doesn't print {check.prints!r}"
            if check.prints is not None
            else f"if `{command}` fails"
        )
    if step.run is not None:
        does = f"{when}, runs: {' '.join(step.run)}"
    elif step.script is not None:
        does = f"{when}, runs the script {step.script} {' '.join(step.args)}".rstrip()
    else:
        return f"report only, {when}"
    if step.stdin is not None:
        does += f" (stdin: {step.stdin})"
    return does


def _planned_detail(entry: StepEntry, found: StepFound, action: Action) -> str:
    asked = f"`untaped dotfiles apply {entry.spelled}`"
    if action == "apply":
        lead = {"new": ADDED, "behind": found.detail, "drifted": found.detail}.get(found.state, "")
        lead = lead or (found.detail if found.state == "pending" else "")
        return "; ".join(filter(None, (lead, describe(entry.step))))
    if action == "remove":
        return f"{found.detail}; {FORGETS}"
    if action == "report" and found.state in ("new", "behind", "drifted", "orphan"):
        detail = found.detail or found.state
        return f"{detail}; {entry.policy} waits: {asked}"
    if action == "skip" and found.state == "behind":
        return f"{found.detail}; {entry.policy} leaves it"
    if action == "skip" and found.state == "applied":
        return "the check passes"
    return found.detail


def _planned_action(plan: StepPlan) -> ApplyAction:
    if plan.found.error is not None:
        return "failed"
    if plan.action in ("apply", "remove"):
        return "planned"
    return "unchanged" if plan.found.state == "applied" else "skipped"


def _kept(plan: StepPlan) -> ApplyAction:
    return "unchanged" if plan.found.state == "applied" else "skipped"


def _failed(plan: StepPlan, exc: UntapedError) -> ApplyOutcome:
    return _row(plan, "failed", str(exc), error=Failure.from_exception(exc, message=str(exc)))


def _row(
    plan: StepPlan, action: ApplyAction, detail: str, *, error: Failure | None = None
) -> ApplyOutcome:
    entry = plan.entry
    return ApplyOutcome(
        source=entry.offer.source.name,
        package=entry.offer.name,
        item=entry.item,
        kind="step",
        entry=entry.step.name,
        action=action,
        state=plan.found.state,
        detail=detail,
        error=error,
    )
