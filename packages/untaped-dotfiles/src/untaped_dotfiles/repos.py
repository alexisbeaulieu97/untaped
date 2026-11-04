"""Working copies: an item's ``repos:`` entries, cloned once and then the person's.

Policy doesn't apply. ``apply`` clones a working copy whose target is absent,
through the git plugin (its credentials and proxy, no helper left behind);
after that untaped never moves it. A checkout of the entry's URL already
there counts as applied (a repo name: a checkout whose ``origin`` ends in that
name, so ``status`` asks no repo source; a bare name matches any owner's repo
of that name), and anything else there is
``foreign`` and never touched. ``remove`` and ``unsubscribe`` forget the rows
and leave the copies.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import ErrorCategory, Failure, UntapedError, run_hint
from untaped_dotfiles.errors import DotfilesError
from untaped_dotfiles.models import RepoRecord
from untaped_dotfiles.records import ApplyAction, ApplyOutcome, EntryStatus
from untaped_dotfiles.states import Action, RepoState, decide_repo
from untaped_git.api import (
    RepoFinder,
    clone,
    git_toplevel,
    looks_like_url,
    repo_url_parts,
    run_git,
    store_key,
)

if TYPE_CHECKING:
    from untaped_dotfiles.catalog import Offer, Selection
    from untaped_dotfiles.inventory import Inventory, Placement
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
    shown: str = ""
    """The target as the person reads it (``~/...``), or as written while it waits."""
    moved_to: str = ""
    """For an ``orphan``: where the entry clones now, when an input moved its target."""
    overlaps: str = ""
    """Another working copy's target at, above or under this one: neither is cloned."""
    files: tuple[str, ...] = ()
    """File targets at or under this target: it can't be cloned there."""

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
    url: str = ""
    """The URL a planned clone fetches (a repo name resolved)."""


class Repos:
    """Lists, evaluates, plans and clones the working copies of the items that are on."""

    def __init__(
        self,
        inventory: Inventory,
        store: DotfilesStore,
        *,
        now: Callable[[], datetime],
    ) -> None:
        self._inventory = inventory
        self._store = store
        self._now = now
        self._finder: RepoFinder | None = None

    # -- entries -------------------------------------------------------------

    def entries(
        self,
        selections: Sequence[Selection] | None = None,
        *,
        placements: Sequence[Placement] = (),
    ) -> list[RepoEntry]:
        """The ``repos:`` entries of the items that are on in ``selections`` (every package
        that is on when ``None``), plus the rows of copies untaped cloned for an entry the
        package dropped (``orphan``) or whose package or item is off now (``off``).
        ``placements``: the files placed with them, whose targets no copy to clone may hold.
        """
        records = {r.target: r for r in self._store.repos()}
        found = self._listed(selections, records)
        everyone = found if selections is None else self._listed(None, records)
        files = [
            p.target
            for p in placements
            if p.target is not None and p.excluded is None and not p.orphan
        ]
        found = self._overlapping(found, everyone, files)
        listed = {str(e.target) for e in found if e.target is not None}
        moved: dict[tuple[str, str], list[str]] = {}
        for e in found:
            if e.target is not None and e.record is None:
                moved.setdefault(("/".join(e.owner), e.repo.url), []).append(e.shown)
        wanted = {(o.source.name, o.name, i): o for o, i in self._inventory.wanted(selections)}
        off = self._inventory.off_scope(selections)
        for record in records.values():
            if record.target not in listed:
                entry = self._unlisted(record, wanted, off)
                if entry is not None and entry.orphan:
                    targets = moved.get((record.item, record.given), [])
                    entry = replace(entry, moved_to=targets[0] if len(targets) == 1 else "")
                found.extend([entry] if entry is not None else [])
        return found

    def _listed(
        self, selections: Sequence[Selection] | None, records: Mapping[str, RepoRecord]
    ) -> list[RepoEntry]:
        """The entries of the items that are on; one waiting on an input keeps the copy cloned
        for it before (its record), as a file's record stays while an input has no value."""
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
                entry = RepoEntry(offer, item, repo, target, waits or "", record, excluded=excluded)
                found.append(entry if target is None else replace(entry, shown=self._show(target)))
        taken = {str(e.target) for e in found if e.target is not None}
        for at, entry in enumerate(found):
            if entry.target is not None:
                continue
            for record in records.values():
                owned = record.item == "/".join(entry.owner) and record.given == entry.repo.url
                if owned and record.target not in taken:
                    target = Path(record.target)
                    found[at] = replace(
                        entry, target=target, waits="", record=record, shown=self._show(target)
                    )
                    taken.add(record.target)
                    break
        return found

    def _show(self, target: Path) -> str:
        return self._inventory.shown(target)

    def _overlapping(
        self, entries: list[RepoEntry], everyone: Sequence[RepoEntry], files: Iterable[Path]
    ) -> list[RepoEntry]:
        """``entries`` with what each copy's target overlaps: another copy's target of any
        package that is on (at, above or under it), or a file's target under it. A copy that
        overlaps is not cloned; one already there stays as it is."""
        active = [e for e in everyone if e.excluded is None and e.target is not None]
        placed = [Path(f) for f in files]
        marked: list[RepoEntry] = []
        for entry in entries:
            if entry.excluded is not None or entry.target is None:
                marked.append(entry)
                continue
            mine = entry.target
            others = [
                o
                for o in active
                if o.target is not None
                and (o.owner, o.repo) != (entry.owner, entry.repo)
                and _nested(mine, o.target)
            ]
            overlaps = ", ".join(f"{o.shown} ({'/'.join(o.owner)})" for o in others)
            inside = tuple(self._show(f) for f in placed if _nested(mine, f, under=True))
            marked.append(replace(entry, overlaps=overlaps, files=inside))
        return marked

    def _unlisted(
        self,
        record: RepoRecord,
        wanted: Mapping[tuple[str, str, str], Offer],
        off: Mapping[tuple[str, str, str], Offer],
    ) -> RepoEntry | None:
        source, package, item = record.item.split("/", 2)
        owned = wanted.get((source, package, item))
        if owned is not None:
            if owned.package is not None and self._inventory.broken(owned) is None:
                return self._from_record(record, owned, item, orphan=True)
            return None
        scope = off.get((source, package, item)) or off.get((source, package, ""))
        if scope is None:
            return None
        if not scope.item_on(item):
            return self._from_record(record, scope, item, off=True)
        return self._from_record(record, scope, item, orphan=True)

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
        shown = self._show(target)
        repo = Repo(url=record.given, target=shown, branch=record.branch)
        return RepoEntry(
            offer, item, repo, target, record=record, orphan=orphan, off=off, shown=shown
        )

    # -- states --------------------------------------------------------------

    def evaluate(self, entry: RepoEntry) -> RepoFound:
        """``entry``'s state: whether its target is absent, a checkout of its URL, or else."""
        if entry.excluded is not None:
            return RepoFound("excluded", entry.excluded)
        if entry.off:
            return RepoFound("off", _off_detail(entry))
        if entry.orphan:
            if entry.moved_to:
                return RepoFound("orphan", f"its target moved to {entry.moved_to}")
            return RepoFound("orphan", "the package no longer has it")
        if entry.target is None:
            return RepoFound("pending", entry.waits)
        found = self._found(entry, entry.target)
        if entry.overlaps and found.state == "pending":
            return _conflict(
                f"{entry.shown} overlaps the working copy at {entry.overlaps}, so it isn't cloned"
            )
        if entry.files and found.state != "applied" and found.error is None:
            return _conflict(
                f"files are placed in {entry.shown} ({', '.join(entry.files)}), "
                "so it can't be cloned there"
            )
        return found

    def _found(self, entry: RepoEntry, target: Path) -> RepoFound:
        if not target.exists() and not target.is_symlink():
            return RepoFound("pending")
        try:
            origin = self._origin(target)
        except UntapedError as exc:
            return RepoFound("error", str(exc), _failure(exc))
        if origin is None:
            return RepoFound("foreign", f"{target} is there and isn't a git checkout")
        given = entry.repo.url
        if looks_like_url(given):
            same = _same_repo(origin, given)
        elif entry.record is not None and entry.record.given == given:
            same = _same_repo(origin, entry.record.url)
        else:  # a repo name, matched offline: status asks no repo source
            same = _named(origin, given)
        if same:
            return RepoFound("applied")
        return RepoFound("foreign", f"{target} is a checkout of {origin}, not {given}")

    def _url(self, entry: RepoEntry) -> str:
        """The URL the entry clones: as written, or a repo name as a repo source lists it (the
        one cloned before, for a copy untaped cloned)."""
        given = entry.repo.url
        if looks_like_url(given):
            return given
        if entry.record is not None and entry.record.given == given:
            return entry.record.url
        if self._finder is None:
            self._finder = RepoFinder()
        return self._finder.resolve(given).url

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
            batch_ssh=False,
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
                    entry=entry.shown or entry.repo.target,
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
        """What ``apply`` does with each working copy: the same whatever the policy. A repo
        name to clone is resolved here, so the preview names the URL."""
        plans: list[RepoPlan] = []
        for entry in entries:
            found = self.evaluate(entry)
            action = _action(entry, found)
            if action != "apply":
                plans.append(RepoPlan(entry, found, action, _planned_detail(entry, found)))
                continue
            try:
                url = self._url(entry)
            except UntapedError as exc:
                if exc.category == ErrorCategory.USAGE:  # a name no repo source lists
                    exc = DotfilesError(str(exc), category="not_found", hint=exc.hint)
                found = RepoFound("error", str(exc), _failure(exc))
                plans.append(RepoPlan(entry, found, "report", str(exc)))
                continue
            branch = f" ({entry.repo.branch})" if entry.repo.branch else ""
            detail = f"clones {url}{branch} into {entry.shown}"
            plans.append(RepoPlan(entry, found, action, detail, url))
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
                rows.append(_row(plan, "unchanged", f"{plan.found.detail}; {KEPT}"))
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
        url = plan.url
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            clone(url, target, branch=entry.repo.branch)
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


def _planned_detail(entry: RepoEntry, found: RepoFound) -> str:
    if entry.orphan:
        return f"{found.detail}; {KEEPS}"
    return found.detail


def _nested(target: Path, other: Path, *, under: bool = False) -> bool:
    """Whether ``other`` is ``target``, under it, or (unless ``under``) above it."""
    return other == target or target in other.parents or (not under and other in target.parents)


def _same_repo(url: str, other: str) -> bool:
    """Whether two URLs name one repo: by host and path, or by path for local ones."""
    host, path = repo_url_parts(url)
    other_host, other_path = repo_url_parts(other)
    if host is None and other_host is None and path and other_path:
        return path == other_path  # a local path: case matters where the filesystem says
    return store_key(url) == store_key(other)


def _named(origin: str, name: str) -> bool:
    """Whether ``origin``'s path ends in the repo ``name`` (``owner/name`` or a bare name),
    case-folded as forges fold names. A bare name matches any owner's repo of that name."""
    _, path = repo_url_parts(origin)
    parts = [part for part in name.split("/") if part]
    return (
        bool(parts) and len(path) >= len(parts) and _folded(path[-len(parts) :]) == _folded(parts)
    )


def _folded(parts: Sequence[str]) -> list[str]:
    return [part.casefold() for part in parts]


def _conflict(message: str) -> RepoFound:
    error = DotfilesError(message, category="conflict")
    return RepoFound("error", str(error), _failure(error))


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
        entry=entry.shown or entry.repo.target,
        action=action,
        state=plan.found.state,
        detail=detail,
        target_path=entry.target,
        error=error,
    )
