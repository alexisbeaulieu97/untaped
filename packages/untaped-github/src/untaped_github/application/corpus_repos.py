"""Getting the repos a corpus use case works on: the scope, then each repo ready on disk.

:func:`resolve_online_scope` lists the repos a sweep or a sync covers, and
:func:`prepare_repo` brings each one's corpus copy up to date (or serves
the copy it has), so a failure names the repo it is about.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from untaped.sdk import System, UntapedError, attribution, bounded_map, most_severe
from untaped.sdk.storage import NO_PROGRESS, Filled, NeverRefreshed, ProgressHandle
from untaped_github.application.inventory import RepositoryInventoryScope
from untaped_github.application.ports import GitCorpus
from untaped_github.domain import (
    ArchivedMode,
    CorpusFailure,
    CorpusFreshness,
    CorpusRepoTarget,
    RefSelector,
    archived_allows,
    covers,
    unchanged_upstream,
)
from untaped_github.domain.errors import is_global_github_failure
from untaped_github.errors import GitCorpusError
from untaped_github.records import Repo
from untaped_github.settings import SWEEP

InventoryResolver = Callable[[RepositoryInventoryScope], tuple[Repo, ...]]
# Parallel per-repo API lookups; kept low to stay clear of GitHub's secondary rate limits.
_API_CONCURRENCY = 4


@dataclass(frozen=True)
class ReadyRepo:
    repo: CorpusRepoTarget
    fetched_at: datetime | None
    refreshed: bool
    refresh_error: CorpusFailure | None = None
    # True when GitHub reported no push since the cached fetch, so none ran.
    unchanged: bool = False


class CorpusUseCase:
    """Shared wiring for use cases that resolve inventory into the local corpus."""

    def __init__(
        self,
        *,
        inventory: InventoryResolver,
        corpus: GitCorpus,
    ) -> None:
        self._inventory = inventory
        self._corpus = corpus


def resolve_online_scope(
    inventory: InventoryResolver,
    *,
    scope: RepositoryInventoryScope,
    stdin_repos: tuple[str, ...],
    stdin_items: tuple[Repo, ...],
    archived: ArchivedMode,
    parallel: int,
) -> tuple[tuple[CorpusRepoTarget, ...], tuple[CorpusFailure, ...]]:
    """Expand scopes through the API into sorted corpus targets plus per-name failures."""
    items: dict[str, Repo] = {}
    if scope.orgs or scope.teams:
        listed = RepositoryInventoryScope(orgs=scope.orgs, teams=scope.teams)
        for item in inventory(listed):
            items.setdefault(item.full_name, item)
    # Piped repo records already carry what a sweep needs: no API call.
    items.update((item.full_name, item) for item in stdin_items)
    # Expand explicit names one at a time so one missing or inaccessible
    # repo becomes an unscanned row instead of aborting the whole sweep.
    # Auth and rate-limit failures hit every repo alike, so they abort.
    failures: list[CorpusFailure] = []
    names = tuple(dict.fromkeys((*scope.repos, *stdin_repos)))

    def resolve_one(name: str) -> tuple[Repo, ...] | CorpusFailure:
        try:
            return inventory(RepositoryInventoryScope(repos=(name,)))
        except UntapedError as exc:
            if is_global_github_failure(exc):
                raise
            return CorpusFailure(repo=name, reason=str(exc) or type(exc).__name__, cause=exc)

    def record(_name: str, resolved: tuple[Repo, ...] | CorpusFailure) -> None:
        if isinstance(resolved, CorpusFailure):
            failures.append(resolved)
        else:
            items.update((item.full_name, item) for item in resolved)

    bounded_map(resolve_one, names, concurrency=min(parallel, _API_CONCURRENCY), on_each=record)
    failures.sort(key=lambda failure: failure.repo)
    if names and len(failures) == len(names) and not items:
        detail = "; ".join(failure.reason for failure in failures)
        raise UntapedError(
            f"no requested repository could be resolved: {detail}",
            **attribution(most_severe(failure.cause for failure in failures)),
        )
    rows = (item for _, item in sorted(items.items()) if archived_allows(archived, item.archived))
    return tuple(_target(item) for item in rows), tuple(failures)


def _target(item: Repo) -> CorpusRepoTarget:
    return CorpusRepoTarget(
        full_name=item.full_name,
        default_branch=item.default_branch,
        clone_url=item.clone_url,
        html_url=item.html_url,
        archived=item.archived,
        pushed_at=item.pushed_at,
    )


def failure_of(repo: CorpusRepoTarget, exc: Exception) -> CorpusFailure:
    return CorpusFailure(repo=repo.full_name, reason=str(exc) or type(exc).__name__, cause=exc)


class _Fetched(Filled):
    """What one fill of :data:`SWEEP` did to a repo's stored copy."""

    fetched_at: datetime | None = None
    # GitHub reported no push since the stored fetch, so none ran.
    unchanged: bool = False


def prepare_repo(
    corpus: GitCorpus,
    repo: CorpusRepoTarget,
    *,
    selector: RefSelector,
    refresh: bool | None,
    progress: ProgressHandle = NO_PROGRESS,
    fetch_progress: bool = False,
    raise_failure: bool = False,
) -> ReadyRepo | CorpusFailure:
    """Bring one repo's stored copy up to date for ``selector`` through :data:`SWEEP`.

    The copy is the data and :data:`SWEEP` keeps when it was last checked
    (:func:`_sync_freshness`). ``serve()`` refreshes as ``refresh`` asks
    (:func:`_filler`); a failed fetch falls back to the stored copy with one
    ``untaped.stale`` line or, with ``raise_failure``, is the repo's
    ``refresh_error`` without one. A copy that does not cover ``selector``
    was never refreshed for this query: it is fetched whatever its age, and
    when that fails, or the copy still does not cover the query after the
    refresh (another run narrowed it meanwhile), the repo is unscanned.
    ``progress`` is the command's task, which each refresh is a phase of;
    ``fetch_progress`` also hands it to the fetch.
    """
    try:
        stored = corpus.repo_freshness(repo)
        key = corpus.cache_key(repo)
    except (GitCorpusError, OSError) as exc:
        return failure_of(repo, exc)
    narrow = refresh is not False and stored is not None and not covers(stored, selector)
    ran: list[str] = []
    failed: list[GitCorpusError] = []
    stopped: list[UntapedError] = []
    fill = _filler(
        corpus,
        repo,
        selector=selector,
        # a copy GitHub reports no push since: its fetch time, kept if the touch fails
        unchanged_since=(
            stored.fetched_at
            if refresh is None and stored and not narrow and unchanged_upstream(stored, repo)
            else None
        ),
        fetch_progress=fetch_progress,
        ran=ran,
        failed=failed,
        stopped=stopped,
    )
    try:
        _sync_freshness(key, stored)
        served = SWEEP.serve(
            {key: repo.full_name},
            refresh=True if narrow else refresh,
            fill=fill,
            read=lambda: None,
            progress=progress,
            # every answer is "empty": a failed refresh raises instead of serving a stale line
            empty=(lambda _: True) if raise_failure or narrow else None,
        )
    except (UntapedError, OSError) as exc:
        _raise_unless_the_repos(stopped)
        remembered = not ran and not isinstance(exc, NeverRefreshed)
        return _not_served(
            repo,
            key,
            # the fetch's own error, not the one serve() rebuilt with a hint to --cached
            failed[-1] if failed else exc,
            remembered=remembered,
            stored=stored if raise_failure else None,
        )
    _raise_unless_the_repos(stopped)
    fetched = served.filled.get(key)
    if fetched is not None:
        return ReadyRepo(
            repo=repo,
            fetched_at=fetched.fetched_at,
            refreshed=not fetched.unchanged,
            unchanged=fetched.unchanged,
        )
    # served as kept: no refresh due, or one another run made while this one waited
    current = _stored(corpus, repo) if refresh is not False else stored
    if current is not None and refresh is not False and not covers(current, selector):
        return failure_of(
            repo, GitCorpusError("another run changed the stored copy's refs; run the sweep again")
        )
    return ReadyRepo(
        repo=repo, fetched_at=current.fetched_at if current is not None else None, refreshed=False
    )


def _not_served(
    repo: CorpusRepoTarget,
    key: str,
    exc: UntapedError | OSError,
    *,
    remembered: bool,
    stored: CorpusFreshness | None,
) -> ReadyRepo | CorpusFailure:
    """What a repo ``serve()`` raised for becomes: unscanned, or a stored copy's failed refresh.

    ``stored`` is passed when the stored copy stands beside the failure (``cache
    sync``'s rows). A ``remembered`` failure is one an earlier run left (no
    fill ran): inside the copy's max age it is no refresh this run asked for,
    so the copy is current and its row says so.
    """
    remembered = remembered and isinstance(exc, UntapedError) and exc.details.get("key") == key
    if remembered and stored is not None:
        state = SWEEP.freshness(key)
        if not state.past_max_age(SWEEP.max_age(), now=datetime.now(UTC)):
            return ReadyRepo(repo=repo, fetched_at=stored.fetched_at, refreshed=False)
    if remembered and isinstance(exc, UntapedError):
        # an earlier run's failed refresh, inside its retry window: no fetch ran
        exc.hint = "pass --refresh to retry it now"
    failure = failure_of(repo, exc)
    if stored is None:
        return failure
    return ReadyRepo(
        repo=repo, fetched_at=stored.fetched_at, refreshed=False, refresh_error=failure
    )


def _filler(
    corpus: GitCorpus,
    repo: CorpusRepoTarget,
    *,
    selector: RefSelector,
    unchanged_since: datetime | None,
    fetch_progress: bool,
    ran: list[str],
    failed: list[GitCorpusError],
    stopped: list[UntapedError],
) -> Callable[[str, ProgressHandle], _Fetched]:
    """The fill of ``repo``'s key: a fetch, or a touch when GitHub reports no push since.

    Each key it starts on goes in ``ran``, each repo's error it raises in
    ``failed``, and each error that is not one repo's (credentials,
    configuration) in ``stopped``.
    """

    def fill(key: str, handle: ProgressHandle) -> _Fetched:
        ran.append(key)
        if unchanged_since is not None:
            try:
                touched = corpus.touch_repo(repo)
            except GitCorpusError, OSError:
                touched = unchanged_since
            return _Fetched(fetched_at=touched, unchanged=True, summary="unchanged")
        try:
            result = corpus.sync_repo(
                repo, selector=selector, progress=handle if fetch_progress else NO_PROGRESS
            )
        except GitCorpusError as exc:
            failed.append(exc)
            raise
        except UntapedError as exc:
            stopped.append(exc)
            raise
        except OSError as exc:
            failed.append(GitCorpusError(str(exc) or type(exc).__name__, system=System.LOCAL))
            raise failed[-1] from exc
        return _Fetched(fetched_at=result.fetched_at)

    return fill


def _raise_unless_the_repos(stopped: list[UntapedError]) -> None:
    """Raise a fill's error that is not one repo's: it stops the run, not the repo.

    ``serve()`` has already remembered it as the key's failed attempt.
    """
    if stopped:
        raise stopped[0]


def _stored(corpus: GitCorpus, repo: CorpusRepoTarget) -> CorpusFreshness | None:
    with contextlib.suppress(GitCorpusError, OSError):
        return corpus.repo_freshness(repo)
    return None


def _sync_freshness(key: str, stored: CorpusFreshness | None) -> None:
    """Bring what :data:`SWEEP` knows of ``key`` in line with the stored copy.

    A copy the cache never saw refreshed was fetched before the cache (or
    by a run that stopped before recording it, or whose first refresh
    failed): it is seeded with its own fetch time. A key known as refreshed
    with no stored copy lost its data (a deleted or released repo), so it is
    forgotten and filled.
    """
    state = SWEEP.freshness(key)
    if stored is None:
        if state.refreshed_at is not None:
            SWEEP.forget(key)
    elif state.refreshed_at is None:
        if state.attempted_at is not None:
            # seed() keeps an existing file: drop the failed first attempt (a fill another
            # run completes meanwhile goes with it, which costs one fetch at worst)
            SWEEP.forget(key)
        SWEEP.seed(key, refreshed_at=stored.fetched_at)
