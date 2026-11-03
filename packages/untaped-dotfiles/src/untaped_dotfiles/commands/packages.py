"""``untaped dotfiles show|add|remove``: the packages the sources offer, turned on or off here."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel, Field

from untaped.sdk import Confirm, DryRun, Output, Positional, UsageError, key, plural, run_hint
from untaped_dotfiles.catalog import Offer, Selection
from untaped_dotfiles.commands._dotfiles import DotfilesCommand, Services, package_row
from untaped_dotfiles.inventory import input_values
from untaped_dotfiles.package import Item
from untaped_dotfiles.policy import Policy
from untaped_dotfiles.records import (
    ApplyOutcome,
    CheckDetail,
    FileDetail,
    InputDetail,
    ItemDetail,
    Package,
    PackageDetail,
    RepoDetail,
    StepDetail,
)
from untaped_dotfiles.settings import DotfilesSettings, ItemChoice, PackageChoice
from untaped_dotfiles.states import FileState

SELECTORS = "Packages or items: PKG, PKG/ITEM, SOURCE/PKG or SOURCE/PKG/ITEM."

#: Which state an entry with several paths (a folder) shows: the one that needs most.
_WORST: tuple[FileState, ...] = (
    "error",
    "conflict",
    "modified",
    "missing",
    "behind",
    "foreign",
    "pending",
    "orphan",
    "applied",
    "excluded",
)


class Add(DotfilesCommand):
    """Turn packages or items on here; the next `apply` places them, whatever their policy.

    `add PKG` turns the package on; `add PKG/ITEM` turns the item on, and turns
    its package on with every other item off when the package was off (said
    in one line). `--policy` makes the policy this machine's choice for the
    package or item. A package this machine can't use (its `os`, `only` or
    `unless`) exits 2, naming why.
    """

    selectors: Positional[list[str]] = Field(min_length=1, description=SELECTORS)
    policy: Policy | None = Field(
        None, description="This machine's policy for them. Default: the author's suggestion."
    )
    dry_run: DryRun
    output: Output[Package]

    def run(self) -> Iterator[Package]:
        svc = self.services()
        with svc.store.locked():
            chosen = svc.catalog.selected(self.selectors)
            for selection in chosen:
                _usable(svc, selection)
            choices: dict[tuple[str, str], tuple[Offer, PackageChoice]] = {}
            notes: list[str] = []
            for selection in chosen:
                offer = selection.offer
                at = (offer.source.name, offer.name)
                current = choices[at][1] if at in choices else offer.choice
                choices[at] = (offer, self._turned_on(selection, current, notes))
            changes = {
                _package_key(offer): choice
                for offer, choice in choices.values()
                if choice != offer.choice
            }
            rows = [
                package_row(_with(offer, choice), svc.inventory.machine)
                for offer, choice in choices.values()
            ]
            if not changes:
                self.ctx.diag.info(f"already on: {', '.join(s.spelled for s in chosen)}")
                return iter(rows)
            plan = self.ctx.plugin.plan_settings(changes)
            if not self.dry_run.go_ahead(preview=plan.preview):
                return iter(rows)
            plan.write()
        policy = f" ({self.policy})" if self.policy is not None else ""
        self.ctx.diag.success(f"turned on {', '.join(s.spelled for s in chosen)}{policy}")
        for note in notes:
            self.ctx.diag.info(note)
        from untaped_dotfiles.commands.apply import Apply  # noqa: PLC0415 - a cycle

        self.ctx.diag.hint(run_hint(Apply.spelled()))
        return iter(rows)

    def _turned_on(
        self, selection: Selection, current: PackageChoice | None, notes: list[str]
    ) -> PackageChoice:
        """``current`` with the selection on (and this run's ``--policy`` on it)."""
        offer, item = selection.offer, selection.item
        package = offer.package
        assert package is not None  # _usable refused a file that doesn't load
        if item is None:
            choice = current or PackageChoice()
            if self.policy is not None:
                choice = choice.model_copy(update={"policy": self.policy})
            return choice
        items = dict(current.items) if current is not None else {}
        if current is None:
            items = {other: ItemChoice(enabled=False) for other in package.items if other != item}
            if items:
                notes.append(f"turned {offer.spelled} on with its other items off")
        own = items.get(item, ItemChoice())
        update: dict[str, Any] = {}
        if not own.enabled:
            update["enabled"] = True
        if self.policy is not None:
            update["policy"] = self.policy
        own = own.model_copy(update=update)
        if own == ItemChoice() and "policy" not in own.model_fields_set:
            items.pop(item, None)
        else:
            items[item] = own
        return (current or PackageChoice()).model_copy(update={"items": items})


class Remove(DotfilesCommand):
    """Take back what packages or items placed here, and turn them off.

    Links and unedited copies are deleted, merged keys taken back out, and a
    file edited on this machine kept aside. `remove PKG` turns the package off;
    `remove PKG/ITEM` turns only the item off. `--keep-files` turns off and
    forgets without touching files. A file that can't be taken back fails its
    row (exit 1), and nothing is turned off.
    """

    selectors: Positional[list[str]] = Field(min_length=1, description=SELECTORS)
    keep_files: bool = Field(False, description="Leave the placed files where they are.")
    confirm: Confirm
    output: Output[ApplyOutcome]

    def run(self) -> Iterator[ApplyOutcome]:
        svc = self.services()
        with svc.store.locked():
            chosen = svc.catalog.selected(self.selectors)
            records = sorted(
                (
                    r
                    for r in svc.store.placed()
                    if any(_owns(s, r.source, r.package, r.item) for s in chosen)
                ),
                key=lambda r: r.target,
            )
            changes: dict[Any, BaseModel | None] = {}
            for selection in chosen:
                offer = selection.offer
                if not offer.on:
                    continue
                if selection.item is None:
                    changes[_package_key(offer)] = None
                    continue
                assert offer.choice is not None
                items = dict(offer.choice.items)
                items[selection.item] = items.get(selection.item, ItemChoice()).model_copy(
                    update={"enabled": False}
                )
                changes[_package_key(offer)] = offer.choice.model_copy(update={"items": items})
            if not changes and not records:
                self.ctx.diag.info(f"already off: {', '.join(s.spelled for s in chosen)}")
                return iter([])
            planned = svc.applier.removal(records, keep_files=self.keep_files)
            plan = self.ctx.plugin.plan_settings(changes) if changes else None
            spelled = ", ".join(s.spelled for s in chosen)

            def preview() -> list[Any]:
                return [*([planned] if planned else []), *(plan.preview() if plan else [])]

            taking = (
                f"take back {plural(len(records), 'file')} and "
                if records and not self.keep_files
                else ""
            )
            if not self.confirm(f"{taking}turn off {spelled}?".capitalize(), preview=preview):
                return iter(planned)
            rows = svc.applier.take_back(records, keep_files=self.keep_files)
            if any(row.failed for row in rows):
                self.report_rows(rows)
                self.ctx.diag.warning(f"{spelled} stays on until every file is taken back")
                return iter(rows)
            if plan is not None:
                plan.write()
            svc.refresh_status()
        self.ctx.diag.success(f"turned off {spelled}")
        return iter(rows)


class Show(DotfilesCommand):
    """Show every package the sources offer, or one package or item in full.

    Each row is a package with its inputs (a secret's value is never shown),
    items, entries and their state here; nothing shown runs a command from a
    source. A package file that doesn't load is a row with `error` (exit 1).
    """

    selector: Positional[str | None] = Field(
        None, description="A package or an item. Default: every package."
    )
    output: Output[PackageDetail]

    def run(self) -> Iterator[PackageDetail]:
        svc = self.services()
        if self.selector is None:
            selections = [Selection(offer) for offer in svc.catalog.offers()]
        else:
            selections = [svc.catalog.select(self.selector)]
        self.report_sources(svc.catalog)
        rows = [_detail(svc, selection) for selection in selections]
        for row in rows:
            if row.error is not None:
                self.ctx.diag.failure(row.error, item=f"{row.source}/{row.name}")
        self.output.empty("No packages: subscribe a source first.")
        return iter(rows)


def _usable(svc: Services, selection: Selection) -> None:
    """Refuse a selection this machine can't turn on: its file doesn't load, or it doesn't apply."""
    offer = selection.offer
    if offer.error is not None:
        raise offer.error
    excluded = offer.exclusion(svc.inventory.machine, selection.item)
    if excluded is not None:
        raise UsageError(f"{selection.spelled} doesn't apply on this machine: {excluded}")


def _package_key(offer: Offer) -> Any:
    return key(DotfilesSettings).sources[offer.source.name].packages[offer.name]


def _with(offer: Offer, choice: PackageChoice) -> Offer:
    from dataclasses import replace  # noqa: PLC0415 - kept beside its only use

    return replace(offer, choice=choice)


def _owns(selection: Selection, source: str, package: str, item: str) -> bool:
    offer = selection.offer
    return (
        offer.source.name == source
        and offer.name == package
        and (selection.item is None or selection.item == item)
    )


def _detail(svc: Services, selection: Selection) -> PackageDetail:
    """The ``PackageDetail`` of a selection (only the selected item, for an item)."""
    offer = selection.offer
    row = package_row(offer, svc.inventory.machine)
    head = PackageDetail(
        source=row.source,
        name=row.name,
        on=row.on,
        policy=row.policy,
        suggested=row.suggested,
        chosen=row.chosen,
        description=row.description,
        excluded=row.excluded,
        error=row.error,
    )
    package = offer.package
    if package is None:
        return head
    answers = offer.choice.inputs if offer.choice is not None else {}
    values = input_values(offer)
    inputs = tuple(
        InputDetail(
            name=name,
            secret=spec.secret,
            default=spec.default,
            value=None if spec.secret else values.get(name),
            origin=None
            if spec.secret or name not in values
            else ("config" if name in answers else "default"),
        )
        for name, spec in package.inputs.items()
    )
    names = [selection.item] if selection.item else list(package.items)
    states = _states(svc, selection)
    items = tuple(_item_detail(svc, offer, name, package.items[name], states) for name in names)
    return head.model_copy(update={"inputs": inputs, "items": items})


def _states(svc: Services, selection: Selection) -> dict[tuple[str, str], FileState]:
    """``(item, entry path) → state`` for the selection's items that are on."""
    resolved = svc.inventory.placements([selection], include_excluded=True)
    svc.evaluator.prime_changes(resolved.placements)
    states: dict[tuple[str, str], FileState] = {}
    for placement in resolved.placements:
        if placement.orphan:
            continue
        state = svc.evaluator.evaluate(placement).state
        at = (placement.item, placement.entry)
        known = states.get(at)
        if known is None or _WORST.index(state) < _WORST.index(known):
            states[at] = state
    return states


def _item_detail(
    svc: Services, offer: Offer, name: str, item: Item, states: dict[tuple[str, str], FileState]
) -> ItemDetail:
    machine = svc.inventory.machine
    on = offer.item_on(name)
    policy, chosen = offer.item_policy(name)
    step_policy = policy if chosen and policy is not None else Policy.MANUAL
    return ItemDetail(
        name=name,
        description=item.description,
        enabled=on,
        policy=policy if on else None,
        excluded=offer.exclusion(machine, name),
        files=tuple(
            FileDetail(
                path=file.path,
                target=file.target,
                mode=file.mode,
                excluded=file.exclusion(machine.os, machine.tags),
                state=states.get((name, file.path)) if on else None,
            )
            for file in item.files
        ),
        steps=tuple(
            StepDetail(
                name=step.name,
                description=step.description,
                policy=step_policy,
                chosen=chosen,
                check=CheckDetail(**step.check.model_dump()) if step.check is not None else None,
                run=step.run,
                script=step.script,
                args=step.args,
                stdin=step.stdin,
                fix=step.fix,
                kind=step.kind,
                excluded=step.exclusion(machine.os, machine.tags),
            )
            for step in item.steps
        ),
        repos=tuple(
            RepoDetail(
                url=repo.url,
                target=repo.target,
                branch=repo.branch,
                excluded=repo.exclusion(machine.os, machine.tags),
            )
            for repo in item.repos
        ),
    )
