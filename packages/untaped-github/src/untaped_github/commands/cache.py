"""``untaped github cache``: the stored copies the sweep reads, in the git plugin's repo store."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Sequence
from typing import Annotated

from pydantic import Field

from untaped.sdk import (
    Confirm,
    Group,
    Output,
    Parallel,
    Positional,
    Refresh,
    UntapedError,
    UsageError,
    ago_text,
    key,
    not_found,
    now,
    plural,
    size_text,
    summary,
)
from untaped_github.application import (
    CleanCorpus,
    CorpusSyncOptions,
    RepositoryInventoryScope,
    ResolveRepositoryInventory,
    StatusCorpus,
    SyncCorpus,
    WorktreeCorpus,
    with_disk_bytes,
)
from untaped_github.commands._github import GithubCommand, Scoped, team_scopes
from untaped_github.commands.sweep import RefProfileOption
from untaped_github.domain import RefSelector
from untaped_github.errors import GithubError
from untaped_github.records import CorpusRepo, Repo, SyncOutcome
from untaped_github.records import Worktree as WorktreeRow
from untaped_github.settings import SWEEP, GithubSettings

#: The columns a list of stored copies shows.
STORED_COLUMNS = ("full_name", "ref", "profile", "archived", "disk_bytes", "fetched_at", "path")


class Status(GithubCommand):
    """List repositories cached in the local corpus.

    One row per repo github has stored, then the count, total size and
    freshness spread on stderr.
    """

    output: Output[CorpusRepo] = Output(columns=STORED_COLUMNS)

    def run(self) -> Iterator[CorpusRepo]:
        self.output.empty("No repositories are cached in the local corpus.")
        rows = StatusCorpus(self.corpus())()
        yield from rows
        total = size_text(sum(row.disk_bytes for row in rows))
        ages = sorted(row.fetched_at for row in rows if row.fetched_at is not None)
        oldest = ago_text(ages[0], now()) if ages else "n/a"
        newest = ago_text(ages[-1], now()) if ages else "n/a"
        self.ctx.diag.info(
            f"Cache: {plural(len(rows), 'repo')}, {total}, oldest {oldest}, newest {newest}"
        )


class Sync(Scoped):
    """Fetch repositories into the local corpus without a query, so later sweeps start warm.

    Run it before a batch of sweeps, or on a schedule. Like ``sweep`` it runs
    ``git``, which must be on ``PATH``, and picks repos to fetch the same way;
    ``--cached`` would fetch nothing, so it is refused. Each row says what
    became of its repo; a failed row exits 1, or 5 when a fetch timed out.

    Examples
    --------
    ```console
    $ untaped github cache sync --org acme
    ```
    """

    refs: RefProfileOption = Field("default", description="Ref profile to fetch.")
    ref: list[str] = Field(
        default_factory=list, description="Additional ref glob to fetch. Repeatable."
    )
    refresh: Refresh = Refresh(SWEEP)
    parallel: Parallel = Parallel(default=key(GithubSettings).sweep.parallel, cap=32)
    output: Output[SyncOutcome]

    def run(self) -> Iterator[SyncOutcome]:
        if self.refresh.directive is False:
            raise UsageError("cache sync fetches repos: drop --cached")
        stdin_repos, stdin_items = self.piped()
        repos = tuple(self.repo)
        piped = bool(stdin_repos or stdin_items)
        orgs = self.orgs(self.scope, scoped=bool(self.scope.team or repos or piped))
        teams = team_scopes(self.scope.team, orgs=orgs)
        if not (orgs or teams or repos or piped):
            raise UsageError(
                "cache sync requires --org, --team, --repo, --stdin, "
                "or a github.default_org setting"
            )
        options = CorpusSyncOptions(
            scope=RepositoryInventoryScope(orgs=orgs, teams=teams, repos=repos),
            stdin_repos=stdin_repos,
            stdin_items=stdin_items,
            archived=self.archived,
            refs=RefSelector(profile=self.refs, globs=tuple(self.ref)),
            refresh=bool(self.refresh.directive),
            parallel=self.parallel.value,
        )
        self.output.empty("No repositories in scope.")
        diag = self.ctx.diag
        with self.client() as client, diag.progress("Syncing repositories") as progress:
            sync = SyncCorpus(inventory=ResolveRepositoryInventory(client), corpus=self.corpus())
            outcomes = sync(options, progress=progress)
        yield from outcomes
        for outcome in outcomes:
            if outcome.error is not None:
                diag.failure(outcome.error, item=outcome.repo)
        diag.info(summary("sync", Counter(outcome.action for outcome in outcomes)))


class _Delete(GithubCommand):
    """Release stored copies: each goes from disk unless something else holds its repo."""

    confirm: Confirm
    output: Output[CorpusRepo]

    def delete(self, selected: Sequence[CorpusRepo]) -> Iterator[CorpusRepo]:
        """Ask, then release ``selected``; a preview's rows say what each would free.

        A repo nobody else uses is ``removed`` (``disk_bytes`` freed); one another
        plugin or a hand-added worktree still uses is ``released`` (``kept`` says who).
        """
        self.output.empty("No matching repositories were cached.")
        if not selected:
            return
        noun = plural(len(selected), "cached GitHub repo")
        preview = lambda: [f"{row.full_name} ({row.ref}): {row.path}" for row in selected]  # noqa: E731
        if not self.confirm(f"Delete {noun}?", preview=preview):
            self.output.columns.add("disk_bytes")  # a preview's table shows what each frees
            yield from (with_disk_bytes(row) for row in selected)
            return
        self.output.columns[:] = ("full_name", "status", "detail")
        cleaner = CleanCorpus(self.corpus())
        for row in selected:
            try:
                released = cleaner(repo=row)
            except UntapedError as exc:
                self.ctx.diag.failure(exc, item=row.full_name)
                continue
            freed = f"{size_text(released.disk_bytes)} freed"
            detail = freed if released.status == "removed" else f"kept: {released.kept}"
            yield released.model_copy(update={"detail": detail})

    def cached(self, orgs: Sequence[str] = ()) -> tuple[CorpusRepo, ...]:
        """The stored copies, of repos owned by one of ``orgs`` (all when none)."""
        rows = self.corpus().list_repos()
        if not orgs:
            return rows
        owners = {org.casefold() for org in orgs}
        return tuple(row for row in rows if row.full_name.partition("/")[0].casefold() in owners)


class Delete(_Delete):
    """Delete cached repositories from the corpus; a repo another plugin uses stays for it.

    untaped github never changes GitHub. This releases github's part of each
    repo: its refs, its ``untaped-github.json`` and its worktrees. A repo
    something else still holds stays, and its row says what. It runs ``git``,
    which must be on ``PATH``. Pass ``OWNER/NAME`` arguments or ``--all``
    (every repo github has stored), not both; ``--org`` narrows the
    selection; there is no ``--team``, because github doesn't record team
    membership. A named repo that isn't cached exits 1 before anything is
    deleted. The next sweep that covers a deleted repo fetches it again.

    Examples
    --------
    ```console
    $ untaped github cache delete acme/api --dry-run
    ```
    """

    repos: Positional[list[str]] = Field(
        default_factory=list, description="Cached repositories (OWNER/NAME) to delete."
    )
    all: Annotated[bool, Field(description="Select every cached repository.")] = False
    org: list[str] = Field(
        default_factory=list, description="Only cached repositories owned by this org. Repeatable."
    )

    def run(self) -> Iterator[CorpusRepo]:
        if self.repos and self.all:
            raise UsageError("pass REPO arguments or --all, not both")
        if not self.repos and not self.all:
            raise UsageError("cache delete requires REPO arguments or --all")
        cached = self.cached(self.org)
        if self.all:
            return self.delete(cached)
        known = {row.full_name.casefold() for row in cached}
        missing = [name for name in self.repos if name.casefold() not in known]
        if missing:
            raise GithubError("; ".join(not_found("cached repo", name) for name in missing))
        requested = {name.casefold() for name in self.repos}
        return self.delete([row for row in cached if row.full_name.casefold() in requested])


class Prune(_Delete):
    """Delete cached repositories that left or were archived in their org (as `cache delete`).

    Examples
    --------
    ```console
    $ untaped github cache prune --org acme --dry-run
    ```
    """

    org: list[str] = Field(
        default_factory=list,
        description=(
            "Org whose departed or archived cached repos to delete. Repeatable; defaults to "
            "github.default_org."
        ),
    )

    def run(self) -> Iterator[CorpusRepo]:
        orgs = tuple(self.org) or (
            (self.settings.default_org,) if self.settings.default_org else ()
        )
        if not orgs:
            raise UsageError("cache prune requires --org or a github.default_org setting")
        cached = self.cached(orgs)
        with self.client() as client, self.ctx.diag.progress("Resolving repository inventory"):
            live = ResolveRepositoryInventory(client)(RepositoryInventoryScope(orgs=orgs))
        return self.delete(_departed_or_archived(cached, live))


class Worktree(GithubCommand):
    """Materialize one cached repo/ref and print the worktree path.

    It checks a stored ref out, detached, under
    ``~/.untaped/plugins/github/worktrees/``, for reading a repo's files;
    ``untaped workspace`` makes checkouts you edit. It runs ``git``, which
    must be on ``PATH``, and works only for refs already stored, and offline
    once their files have been fetched. ``--format raw`` prints just the
    path, for ``$(…)``.
    """

    repo: Positional[str] = Field(description="Repository OWNER/NAME.")
    ref: str | None = Field(None, description="Cached ref to materialize.")
    output: Output[WorktreeRow]

    def run(self) -> WorktreeRow:
        with self.ctx.diag.progress("Materializing worktree"):
            return WorktreeCorpus(self.corpus())(self.repo, ref=self.ref)


class CacheGroup(Group):
    """Inspect and manage the local Git corpus cache."""

    commands = (Status, Sync, Delete, Prune, Worktree)


def _departed_or_archived(
    cached: tuple[CorpusRepo, ...], live: tuple[Repo, ...]
) -> tuple[CorpusRepo, ...]:
    live_by_name = {row.full_name: row for row in live}
    return tuple(
        row
        for row in cached
        if row.full_name not in live_by_name or live_by_name[row.full_name].archived
    )
