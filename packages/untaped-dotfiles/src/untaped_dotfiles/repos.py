"""Working copies: an item's ``repos:`` entries, cloned once and then the person's.

Policy doesn't apply. ``apply`` clones a working copy whose target is absent,
through the git plugin (its credentials and proxy, no helper left behind);
after that untaped never moves it. A checkout of the entry's URL already
there counts as applied, and anything else there is ``foreign`` and never
touched. ``remove`` and ``unsubscribe`` forget the rows and leave the copies.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import Failure, UntapedError, run_hint
from untaped_dotfiles.errors import DotfilesError
from untaped_dotfiles.models import RepoRecord
from untaped_dotfiles.records import ApplyAction, ApplyOutcome, EntryStatus
from untaped_dotfiles.states import Action, RepoState, decide_repo
from untaped_git.api import RepoFinder, clone, git_toplevel, looks_like_url, run_git, store_key

if TYPE_CHECKING:
    from untaped_dotfiles.catalog import Offer, Selection
    from untaped_dotfiles.inventory import Inventory
    from untaped_dotfiles.package import Repo
    from untaped_dotfiles.ports import DotfilesStore

#: The detail of a working copy untaped forgets: the folder is never deleted.
KEPT = "forgotten; the working copy stays, it is yours"
KEEPS = "forgets it; the working copy stays, it is yours"

_GIT_TIMEOUT = 30.0


@dataclass(frozen=True)
class RepoEntry:
    """One ``repos:`` entry of an item that is on, or a row its package no longer has."""

    offer: Offer
    item: str
    repo: Repo
    target: Path | None
    """Where the copy goes; ``None`` while an input the target names has no value."""
    waits: str = ""
    """Why the target is unknown (an input with no value)."""
    record: RepoRecord | None = None
    excluded: str | None = None
    off: bool = False
    orphan: bool = False

    @property
    def owner(self) -> tuple[str, str, str]:
        return (self.offer.source.name, self.offer.name, self.item)


@dataclass(frozen=True)
class RepoFound:
    state: RepoState
    detail: str = ""
    error: Failure | None = None


@dataclass(frozen=True)
class RepoPlan:
    entry: RepoEntry
    found: RepoFound
    action: Action
    detail: str


class Repos:
    """Lists, evaluates, plans and clones the working copies of the items that are on."""

    def __init__(
        self,
        inventory: Inventory,
        store: DotfilesStore,
        *,
        now: Callable[[], datetime],
        resolve: Callable[[str], str] | None = None,
        cloner: Callable[[str, Path, str | None], None] | None = None,
    ) -> None:
        self._inventory = inventory
        self._store = store
        self._now = now
        self._resolve = resolve or (lambda name: RepoFinder().resolve(name).url)
        self._clone = cloner or (lambda url, dest, branch: clone(url, dest, branch=branch))

    # -- entries -------------------------------------------------------------

    def entries(self, selections: Sequence[Selection] | None = None) -> list[RepoEntry]:
        """The ``repos:`` entries of the items that are on in ``selections`` (every package
        that is on when ``None``), plus the rows of copies untaped cloned for an entry the
        package dropped (``orphan``) or whose package or item is off now (``off``)."""
        records = {r.target: r for r in self._store.repos()}
        found: list[RepoEntry] = []
        machine = self._inventory.machine
        for offer, item in self._inventory.wanted(selections):
            package = offer.package
            if package is None or offer.gone or self._inventory.broken(offer) is not None:
                continue
            reason = offer.exclusion(machine, item)
            for repo in package.items[item].repos:
                excluded = reason or repo.exclusion(machine.os, machine.tags)
                try:
                    target, waits = self._inventory.render_target(offer, repo.target)
                except UntapedError as exc:
                    raise DotfilesError(str(exc)) from exc
                record = records.get(str(target)) if target is not None else None
                found.append(
                    RepoEntry(offer, item, repo, target, waits or "", record, excluded=excluded)
                )
        listed = {str(e.target) for e in found if e.target is not None}
        wanted = {(o.source.name, o.name, i): o for o, i in self._inventory.wanted(selections)}
        off = self._inventory.off_scope(selections)
        for record in records.values():
            if record.target not in listed:
                found.extend(self._unlisted(record, wanted, off))
        return found

    def _unlisted(
        self,
        record: RepoRecord,
        wanted: Mapping[tuple[str, str, str], Offer],
        off: Mapping[tuple[str, str, str], Offer],
    ) -> list[RepoEntry]:
        source, package, item = record.item.split("/", 2)
        owned = wanted.get((source, package, item))
        if owned is not None:
            if owned.package is not None and self._inventory.broken(owned) is None:
                return [self._from_record(record, owned, item, orphan=True)]
            return []
        scope = off.get((source, package, item)) or off.get((source, package, ""))
        if scope is None:
            return []
        if not scope.item_on(item):
            return [self._from_record(record, scope, item, off=True)]
        return [self._from_record(record, scope, item, orphan=True)]

    def _from_record(
        self,
        record: RepoRecord,
        offer: Offer,
        item: str,
        *,
        orphan: bool = False,
        off: bool = False,
    ) -> RepoEntry:
        from untaped_dotfiles.package import Repo  # noqa: PLC0415 - only for a row's own entry

        target = Path(record.target)
        shown = self._inventory.shown(target)
        repo = Repo(url=record.given, target=shown, branch=record.branch)
        return RepoEntry(offer, item, repo, target, record=record, orphan=orphan, off=off)

    # -- states --------------------------------------------------------------

    def evaluate(self, entry: RepoEntry) -> RepoFound:
        """``entry``'s state: whether its target is absent, a checkout of its URL, or else."""
        if entry.excluded is not None:
            return RepoFound("excluded", entry.excluded)
        if entry.off:
            return RepoFound("off", _off_detail(entry))
        if entry.orphan:
            return RepoFound("orphan", "the package no longer has it")
        if entry.target is None:
            return RepoFound("pending", entry.waits)
        target = entry.target
        if not target.exists() and not target.is_symlink():
            return RepoFound("pending")
        try:
            origin = self._origin(target)
            if origin is None:
                return RepoFound("foreign", f"{target} is there and isn't a git checkout")
            url = self.url(entry)
        except UntapedError as exc:
            return RepoFound("error", str(exc), Failure.from_exception(exc, message=str(exc)))
        if store_key(origin) == store_key(url):
            return RepoFound("applied")
        return RepoFound("foreign", f"{target} is a checkout of {origin}, not {url}")

    def url(self, entry: RepoEntry) -> str:
        """The URL the entry clones: as written, or a repo name as a repo source lists it (the
        one cloned before, for a copy untaped cloned)."""
        given = entry.repo.url
        if looks_like_url(given):
            return given
        if entry.record is not None and entry.record.given == given:
            return entry.record.url
        return self._resolve(given)

    @staticmethod
    def _origin(target: Path) -> str | None:
        """The ``origin`` URL of the checkout whose root is ``target``, or ``None``."""
        if not target.is_dir() or git_toplevel(target, timeout=_GIT_TIMEOUT) != target.resolve():
            return None
        ran = run_git(  # as written: get-url would apply this machine's insteadOf rewrites
            ["config", "--get", "remote.origin.url"],
            cwd=target,
            timeout=_GIT_TIMEOUT,
            capture=True,
            check=False,
        )
        url = ran.text.strip() if ran.returncode == 0 else ""
        return url or None

    # -- status and plans ----------------------------------------------------

    def status_rows(self, entries: Sequence[RepoEntry]) -> tuple[list[EntryStatus], list[Action]]:
        rows: list[EntryStatus] = []
        actions: list[Action] = []
        for entry in entries:
            found = self.evaluate(entry)
            rows.append(
                EntryStatus(
                    source=entry.offer.source.name,
                    package=entry.offer.name,
                    item=entry.item,
                    kind="repo",
                    entry=entry.repo.target,
                    policy=None,
                    state=found.state,
                    detail=found.detail,
                    target_path=entry.target,
                    error=found.error,
                )
            )
            actions.append(_action(entry, found))
        return rows, actions

    def plan(self, entries: Sequence[RepoEntry]) -> list[RepoPlan]:
        """What ``apply`` does with each working copy: the same whatever the policy."""
        plans: list[RepoPlan] = []
        for entry in entries:
            found = self.evaluate(entry)
            action = _action(entry, found)
            plans.append(RepoPlan(entry, found, action, _planned_detail(entry, found, action)))
        return plans

    @staticmethod
    def planned(plans: Sequence[RepoPlan]) -> list[ApplyOutcome]:
        return [_row(p, _planned_action(p), p.detail, error=p.found.error) for p in plans]

    @staticmethod
    def waiting(plans: Sequence[RepoPlan]) -> int:
        """How many copies wait for a person: ``apply`` exits 3 for them."""
        return sum(1 for p in plans if p.action == "report" and p.found.error is None)

    def forgetting(
        self, prefixes: Sequence[tuple[str, ...]], *, done: bool = False
    ) -> list[ApplyOutcome]:
        """A row for each copy untaped cloned under ``prefixes`` that turning them off
        forgets; ``done``: they were. The copies stay."""
        starts = ["/".join(prefix) for prefix in prefixes]
        rows: list[ApplyOutcome] = []
        for record in self._store.repos():
            if not any(record.item == s or record.item.startswith(s + "/") for s in starts):
                continue
            source, package, item = record.item.split("/", 2)
            target = Path(record.target)
            rows.append(
                ApplyOutcome(
                    source=source,
                    package=package,
                    item=item,
                    kind="repo",
                    entry=self._inventory.shown(target),
                    action="unchanged",
                    detail=KEPT if done else KEEPS,
                    target_path=target,
                )
            )
        return rows

    # -- running -------------------------------------------------------------

    def execute(self, plans: Sequence[RepoPlan]) -> tuple[list[ApplyOutcome], int]:
        """Clone every planned copy, after the confirmation; the rows, and how many copies
        wait for a person."""
        rows: list[ApplyOutcome] = []
        waiting = 0
        for plan in plans:
            if plan.found.error is not None:
                rows.append(_row(plan, "failed", plan.detail, error=plan.found.error))
            elif plan.action == "remove":
                if plan.entry.record is not None:
                    self._store.forget_repo(plan.entry.record.target)
                rows.append(_row(plan, "deleted", KEPT))
            elif plan.action == "apply":
                rows.append(self._cloned(plan))
            else:
                waiting += plan.action == "report"
                rows.append(_row(plan, _planned_action(plan), plan.detail))
        return rows, waiting

    def _cloned(self, plan: RepoPlan) -> ApplyOutcome:
        entry = plan.entry
        target = entry.target
        assert target is not None  # a target waiting on an input is reported, never cloned
        try:
            url = self.url(entry)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._clone(url, target, entry.repo.branch)
        except OSError as exc:
            failure = DotfilesError(f"could not make {target.parent}: {exc.strerror or exc}")
            return _row(plan, "failed", str(failure), error=_failure(failure))
        except UntapedError as exc:
            return _row(plan, "failed", str(exc), error=_failure(exc))
        self._store.put_repo(
            RepoRecord(
                target=str(target),
                item="/".join(entry.owner),
                given=entry.repo.url,
                url=url,
                branch=entry.repo.branch,
                cloned_at=self._now(),
            )
        )
        return _row(plan, "created", f"cloned {url}")


def _action(entry: RepoEntry, found: RepoFound) -> Action:
    if found.error is not None or (entry.target is None and found.state == "pending"):
        return "report"
    return decide_repo(found.state)


def _off_detail(entry: RepoEntry) -> str:
    from untaped_dotfiles.commands.packages import Remove  # noqa: PLC0415 - a cycle

    offer = entry.offer
    which = "package" if not offer.on else "item"
    spelled = offer.spelled if not offer.on else f"{offer.spelled}/{entry.item}"
    forget = run_hint(Remove.spelled(spelled))
    return f"cloned earlier; the {which} is off now; to forget it (the copy stays), {forget}"


def _planned_detail(entry: RepoEntry, found: RepoFound, action: Action) -> str:
    if action == "apply":
        branch = f" ({entry.repo.branch})" if entry.repo.branch else ""
        return f"clones {entry.repo.url}{branch}"
    if action == "remove":
        return f"{found.detail}; {KEEPS}"
    return found.detail


def _planned_action(plan: RepoPlan) -> ApplyAction:
    if plan.found.error is not None:
        return "failed"
    if plan.action in ("apply", "remove"):
        return "planned"
    return "unchanged" if plan.found.state == "applied" else "skipped"


def _failure(exc: UntapedError) -> Failure:
    return Failure.from_exception(exc, message=str(exc))


def _row(
    plan: RepoPlan, action: ApplyAction, detail: str, *, error: Failure | None = None
) -> ApplyOutcome:
    entry = plan.entry
    return ApplyOutcome(
        source=entry.offer.source.name,
        package=entry.offer.name,
        item=entry.item,
        kind="repo",
        entry=entry.repo.target,
        action=action,
        state=plan.found.state,
        detail=detail,
        target_path=entry.target,
        error=error,
    )
