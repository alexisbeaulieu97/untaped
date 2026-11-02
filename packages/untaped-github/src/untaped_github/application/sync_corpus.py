"""``github cache sync``: warm the corpus copies of the repos in scope, one outcome row each."""

from __future__ import annotations

from dataclasses import dataclass

from untaped.sdk import Failure, bounded_map
from untaped.sdk.storage import NO_PROGRESS, ProgressHandle
from untaped_github.application.corpus_repos import (
    CorpusUseCase,
    ReadyRepo,
    prepare_repo,
    resolve_online_scope,
)
from untaped_github.application.inventory import RepositoryInventoryScope
from untaped_github.domain import ArchivedMode, CorpusFailure, CorpusRepoTarget, RefSelector
from untaped_github.records import Repo, SyncOutcome


@dataclass(frozen=True)
class CorpusSyncOptions:
    """Options for warming the corpus without a query (``cache sync``)."""

    scope: RepositoryInventoryScope
    stdin_repos: tuple[str, ...]
    archived: ArchivedMode
    refs: RefSelector
    refresh: bool
    parallel: int
    stdin_items: tuple[Repo, ...] = ()


class SyncCorpus(CorpusUseCase):
    """Fetch every repository in scope into the corpus so later sweeps start warm."""

    def __call__(
        self, options: CorpusSyncOptions, *, progress: ProgressHandle = NO_PROGRESS
    ) -> tuple[SyncOutcome, ...]:
        progress.update(phase="resolving repositories")
        repos, failures = resolve_online_scope(
            self._inventory,
            scope=options.scope,
            stdin_repos=options.stdin_repos,
            stdin_items=options.stdin_items,
            archived=options.archived,
            parallel=options.parallel,
        )
        outcomes = [
            SyncOutcome(
                repo=failure.repo,
                action="failed",
                detail=failure.reason,
                error=_failure_error(failure),
            )
            for failure in failures
        ]

        def sync_one(repo: CorpusRepoTarget) -> ReadyRepo | CorpusFailure:
            return prepare_repo(
                self._corpus,
                repo,
                selector=options.refs,
                refresh=True if options.refresh else None,
                progress=progress,
                # One repo: its fetch's phases are the line; many: the count is.
                fetch_progress=len(repos) == 1,
                # A failed fetch is the row's failure, never a stale line: no data is served.
                raise_failure=True,
            )

        def record(repo: CorpusRepoTarget, result: ReadyRepo | CorpusFailure) -> None:
            outcomes.append(_sync_outcome(repo, result))
            done = len(outcomes) - len(failures)
            fetched = sum(outcome.action == "synced" for outcome in outcomes)
            failed = sum(outcome.failed for outcome in outcomes)
            progress.update(
                f"{fetched} fetched, {failed} failed",
                phase="syncing",
                done=done,
                total=len(repos),
            )

        if repos:
            progress.update(phase="syncing", done=0, total=len(repos))
        bounded_map(sync_one, repos, concurrency=options.parallel, on_each=record)
        return tuple(sorted(outcomes, key=lambda outcome: outcome.repo))


def _sync_outcome(repo: CorpusRepoTarget, result: ReadyRepo | CorpusFailure) -> SyncOutcome:
    if isinstance(result, CorpusFailure):
        return SyncOutcome(
            repo=repo.full_name,
            action="failed",
            detail=result.reason,
            error=_failure_error(result),
        )
    if result.refresh_error is not None:
        return SyncOutcome(
            repo=repo.full_name,
            action="failed",
            fetched_at=result.fetched_at,
            detail=result.refresh_error.reason,
            error=_failure_error(result.refresh_error),
        )
    action = "synced" if result.refreshed else "unchanged" if result.unchanged else "skipped"
    return SyncOutcome(repo=repo.full_name, action=action, fetched_at=result.fetched_at)


def _failure_error(failure: CorpusFailure) -> Failure:
    """The ``error`` of a failed sync row (counted toward the run's exit code)."""
    return Failure.from_exception(failure.cause, message=failure.reason)
