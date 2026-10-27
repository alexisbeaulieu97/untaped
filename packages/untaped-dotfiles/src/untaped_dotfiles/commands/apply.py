"""``untaped dotfiles apply|sync|remove``: place, follow and take back the enabled items' paths.

Each holds the plugin's ``sync`` lock, so a timer's ``sync`` and a manual
``apply`` never interleave, and each rewrites status.json and attention
after it changed something.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from pydantic import Field

from untaped.sdk import Confirm, DryRun, Matched, Output, Positional, plural
from untaped_dotfiles.applier import Step
from untaped_dotfiles.choices import pick_enabled
from untaped_dotfiles.commands._dotfiles import Chosen, DotfilesCommand, Selected, Services
from untaped_dotfiles.inventory import Placement
from untaped_dotfiles.models import ItemChoice
from untaped_dotfiles.records import (
    ApplyOutcome,
    PlaceOutcome,
    RemoveOutcome,
    RepoOutcome,
    SyncOutcome,
)
from untaped_dotfiles.states import needs_attention


class Apply(Selected):
    """Place the named enabled items (all of them by default), whatever their policy.

    Fetches every repo the items read from first, so a managed clone's
    manifest, copy and merge files are read as its remote holds them now; a
    failed fetch is reported and exits 1 (a --dry-run preview only warns).
    It can move other items' link files; the plan lists them. A local file in
    the way of a new path is kept aside, and the row says where. Refuses
    paths edited on this machine (row action `conflict`, exit 1, under
    --dry-run too) unless --force; `diff` shows what --force would replace in
    copy and merge files as of the last fetch. The policy guide explains what
    `apply` does in each state and when a clone is pulled.
    """

    force: bool = Field(
        False,
        description="Replace paths edited on this machine too (the local version is kept aside).",
    )
    confirm: Confirm
    output: Output[ApplyOutcome]

    def run(self) -> Iterator[ApplyOutcome]:
        svc = self.services()
        with svc.store.locked():
            choices = pick_enabled(svc.store, self.items, repo=self.repo)
            unfetched = _fetch_repos(svc, choices)
            resolved = svc.inventory.placements(choices)  # manifests as just fetched
            self.report(resolved.problems)
            placements = resolved.placements
            pulled = _plan_pulls(placements, unfetched)
            moved = _moving_with(svc, pulled, placements)
            steps = svc.applier.plan_apply(placements, force=self.force)
            planned = svc.applier.planned(steps) + moved
            todo = [s for s in steps if s.action in ("apply", "remove")]
            for repo_row in unfetched.values():
                error = repo_row.error
                assert error is not None  # fetch_all returns failures only
                if self.confirm.dry_run:
                    self.ctx.diag.warning(f"{repo_row.name}: {error.message}", hint=error.hint)
                else:
                    self.ctx.diag.failure(error, item=repo_row.name)
            if not self.confirm(
                f"Apply {plural(len(todo), 'path')}?",
                preview=lambda: plan_lines(svc, planned),
                needed=bool(todo or moved),
            ):
                return iter(_as(ApplyOutcome, planned))
            if todo or moved:
                _fast_forward(svc, pulled, placements)
                steps = svc.applier.replan(steps, force=self.force)
            rows = svc.applier.execute(steps) + moved
            _refresh_status(svc)
        self.output.empty("Nothing to apply.")
        self.report_rows(rows)
        for pull in pulled.values():
            if pull.error is not None:
                self.ctx.diag.failure(pull.error, item=pull.name)
        return iter(_as(ApplyOutcome, rows))


class Sync(DotfilesCommand):
    """Fetch every repo, apply the `sync` items, report the rest. Safe for a timer.

    Never prompts and never overwrites a path edited on this machine. Exits 3
    when anything needs the user, 1 when an apply or a git operation failed.
    Rewrites status.json and attention unless --dry-run. The policy guide
    has what each policy does in each state, and when a clone is pulled.
    """

    dry_run: DryRun
    output: Output[SyncOutcome]

    def run(self) -> Iterator[SyncOutcome]:
        rows, repo_rows, attention = self._sync()
        self.output.empty("Nothing enabled.")
        for row in repo_rows:
            if row.action == "skipped":
                self.ctx.diag.warning(f"{row.name}: {row.detail}")
            if row.error is not None:
                self.ctx.diag.failure(row.error, item=row.name)
        self.report_rows(rows)
        yield from _as(SyncOutcome, rows)
        if attention > 0:
            raise Matched

    def _sync(self) -> tuple[list[PlaceOutcome], list[RepoOutcome], int]:
        """The rows, each repo's fetch or fast-forward, and how many paths need the user."""
        svc = self.services()
        with svc.store.locked():
            choices = svc.store.items()
            repos = svc.store.repos()
            repo_rows = {r.name: r for r in svc.applier.fetch_all(repos)}
            resolved = svc.inventory.placements(choices)  # manifests as just fetched
            self.report(resolved.problems)
            placements = resolved.placements
            if not self.dry_run:
                for repo in repos:
                    if repo.name not in repo_rows:
                        held = svc.applier.held_back_by(repo, placements)
                        repo_rows[repo.name] = svc.applier.fast_forward(repo, held=held)
            svc.evaluator.reset()
            steps = svc.applier.plan_sync(placements)
            if self.dry_run:
                return svc.applier.planned(steps), list(repo_rows.values()), _attention(steps)
            rows = svc.applier.execute(steps)
            # removed orphans no longer have a placement, so resolve again for the status
            status_rows = svc.reader.rows(svc.inventory.placements(choices).placements)
            summary = svc.reader.summary(status_rows, repos_behind=svc.reader.repos_behind())
            svc.store.write_status(summary)
            return rows, list(repo_rows.values()), summary.attention


class Remove(Chosen):
    """Delete the paths the tool placed for items and disable them.

    Links and unedited copies are deleted, merged keys are taken back out,
    and a copy edited on this machine is kept aside instead of deleted.
    """

    items: Positional[list[str]] = Field(
        default_factory=list, description="Enabled items whose paths to remove."
    )
    confirm: Confirm
    output: Output[RemoveOutcome]

    def run(self) -> Iterator[RemoveOutcome]:
        names = self.names(self.items)
        svc = self.services()
        with svc.store.locked():
            choices = pick_enabled(svc.store, names, repo=self.repo)
            resolved = svc.inventory.placements(choices)
            self.report(resolved.problems)
            steps = svc.applier.plan_apply(resolved.placements, force=True)
            planned = svc.applier.preview_removal(steps)
            recorded = [s for s in steps if s.found.record is not None]
            question = (
                f"Remove {plural(len(recorded), 'path')} and disable "
                f"{plural(len(choices), 'item')}?"
            )
            if not self.confirm(question, preview=lambda: plan_lines(svc, planned)):
                return iter(_as(RemoveOutcome, planned))
            rows = svc.applier.remove_all(steps)
            for choice in choices:
                svc.store.remove_item(choice.repo, choice.name)
            _refresh_status(svc)
        self.output.empty("Nothing to remove.")
        self.report_rows(rows)
        return iter(_as(RemoveOutcome, rows))


def plan_lines(svc: Services, planned: Sequence[PlaceOutcome]) -> list[str]:
    """The plan a question shows on stderr: aligned columns, a column empty on every row left out.

    Text for the person answering, whatever ``--format`` is; the rows go to
    stdout once the command runs.
    """
    columns: dict[str, list[str]] = {
        "item": [row.item for row in planned],
        "source": [row.source for row in planned],
        "action": [row.action for row in planned],
        "state": [row.state or "" for row in planned],
        "target": [svc.shown(row.target_path) for row in planned],
        "detail": [row.detail for row in planned],
    }
    shown = [[name, *values] for name, values in columns.items() if any(values)]
    widths = [max(map(len, column)) for column in shown]
    return [
        "  ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=True)).rstrip()
        for line in zip(*shown, strict=True)
    ]


def _refresh_status(svc: Services) -> None:
    """Rewrite status.json and attention after a change, so the prompt segment is current."""
    svc.evaluator.reset()
    rows = svc.reader.rows(svc.inventory.placements(svc.store.items()).placements)
    svc.store.write_status(svc.reader.summary(rows, repos_behind=svc.reader.repos_behind()))


def _fetch_repos(svc: Services, choices: Sequence[ItemChoice]) -> dict[str, RepoOutcome]:
    """Fetch every repo the chosen items read from; return the repos whose fetch failed."""
    names = {c.repo for c in choices}
    repos = [r for r in svc.store.repos() if r.name in names]
    unfetched = {row.name: row for row in svc.applier.fetch_all(repos)}
    svc.evaluator.reset()
    return unfetched


def _plan_pulls(
    placements: Sequence[Placement], unfetched: dict[str, RepoOutcome]
) -> dict[str, RepoOutcome]:
    """The managed clones the selected link files read from; each moves later if fetched."""
    names = {p.repo.name for p in placements if p.mode == "link" and p.repo.managed}
    return {
        name: RepoOutcome(name=name, action="planned", detail="fast-forward")
        for name in names
        if name not in unfetched
    }


def _fast_forward(
    svc: Services, pulled: dict[str, RepoOutcome], placements: Sequence[Placement]
) -> None:
    """Fast-forward every clone ``_plan_pulls`` planned; ``apply`` never holds one back."""
    for name in list(pulled):
        repo = next(p.repo for p in placements if p.repo.name == name)
        pulled[name] = svc.applier.fast_forward(repo)
    svc.evaluator.reset()


def _moving_with(
    svc: Services, pulled: dict[str, RepoOutcome], selected: Sequence[Placement]
) -> list[PlaceOutcome]:
    """Link paths of other items that move when the selected items' clones are pulled."""
    if not pulled:
        return []
    chosen = {p.key for p in selected}
    every = svc.inventory.placements(svc.store.items()).placements
    others = [
        p
        for p in every
        if p.mode == "link" and p.repo.name in pulled and p.key not in chosen and not p.excluded
    ]
    if not others:
        return []
    svc.evaluator.prime_changes(others)
    rows: list[PlaceOutcome] = []
    for p in others:
        found = svc.evaluator.evaluate(p)
        if found.state in ("behind", "conflict"):
            rows.append(
                PlaceOutcome(
                    repo=p.repo.name,
                    item=p.item,
                    source=p.source,
                    mode=p.mode,
                    action="updated",
                    state=found.state,
                    detail=f"moves with the clone of {p.repo.name} ({p.choice.policy} item)",
                    target_path=p.target,
                )
            )
    return rows


def _as[T: PlaceOutcome](model: type[T], rows: Sequence[PlaceOutcome]) -> list[T]:
    """The applier's rows as the command's own outcome kind (the fields are the same)."""
    return [model.model_validate(row, from_attributes=True) for row in rows]


def _attention(steps: Sequence[Step]) -> int:
    """How many reported paths need the user, as ``sync --dry-run`` plans them."""
    return sum(
        1
        for s in steps
        if s.action == "report" and needs_attention(s.placement.choice.policy, s.found.state)
    )
