"""Use case: reconcile selected workspace repos through one global queue."""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Protocol

from untaped_workspace.application.ports import ManifestReader
from untaped_workspace.application.repo_selector import select_repos
from untaped_workspace.application.sync_workspace import BareFetchTracker, RepoSyncEngine
from untaped_workspace.domain import Repo, SyncOutcome, Workspace, WorkspaceManifest
from untaped_workspace.errors import UnmatchedRepoFilter, WorkspaceError


class ProgressNotify(Protocol):
    def __call__(
        self, message: str, *, fraction: float | None = None, new_phase: bool = False
    ) -> None: ...


@dataclass(frozen=True)
class RepoSyncJob:
    """One planned repo sync operation with its output-order ordinal."""

    ordinal: int
    workspace: Workspace
    manifest: WorkspaceManifest
    repo: Repo


@dataclass(frozen=True)
class _PlannedOutcome:
    ordinal: int
    outcome: SyncOutcome


@dataclass(frozen=True)
class _PruneTarget:
    workspace: Workspace
    manifest: WorkspaceManifest


@dataclass(frozen=True)
class _SyncPlan:
    rows: list[_PlannedOutcome]
    jobs: list[RepoSyncJob]
    prune_targets: list[_PruneTarget]


class SyncWorkspaces:
    def __init__(
        self,
        manifests: ManifestReader,
        engine: RepoSyncEngine,
        *,
        notify: ProgressNotify | None = None,
    ) -> None:
        self._manifests = manifests
        self._engine = engine
        self._notify = notify

    def __call__(
        self,
        workspaces: Sequence[Workspace],
        *,
        only: Sequence[str] | None = None,
        prune: bool = False,
        strict_only: bool = True,
        parallel: int = 1,
        bare_tracker: BareFetchTracker | None = None,
    ) -> list[SyncOutcome]:
        tracker = bare_tracker if bare_tracker is not None else BareFetchTracker()
        plan = self._build_plan(workspaces, only=only, prune=prune, strict_only=strict_only)
        planned: list[_PlannedOutcome] = list(plan.rows)
        unexpected: list[tuple[RepoSyncJob, Exception]] = []
        if plan.jobs:
            if parallel <= 1 or len(plan.jobs) <= 1:
                rows, unexpected = self._run_serial(plan.jobs, tracker)
                planned.extend(rows)
            else:
                self._notify_start(total=len(plan.jobs), workers=parallel)
                rows, unexpected = self._run_parallel(plan.jobs, tracker, parallel)
                planned.extend(rows)
            if unexpected:
                raise _unexpected_sync_error(unexpected)

        outcomes = [row.outcome for row in sorted(planned, key=lambda row: row.ordinal)]
        if prune:
            for target in plan.prune_targets:
                outcomes.extend(self._engine.prune_orphans(target.workspace, target.manifest))
        return outcomes

    def _build_plan(
        self,
        workspaces: Sequence[Workspace],
        *,
        only: Sequence[str] | None,
        prune: bool,
        strict_only: bool,
    ) -> _SyncPlan:
        rows: list[_PlannedOutcome] = []
        jobs: list[RepoSyncJob] = []
        prune_targets: list[_PruneTarget] = []
        unmatched_errors: list[str] = []
        ordinal = 0
        for workspace in workspaces:
            manifest = self._manifests.read(workspace.path)
            repos, unmatched = select_repos(manifest, only)
            if unmatched and strict_only:
                unmatched_errors.extend(unmatched)
            if not strict_only:
                for identifier in unmatched:
                    rows.append(
                        _PlannedOutcome(
                            ordinal=ordinal,
                            outcome=SyncOutcome(
                                workspace=workspace.name,
                                repo=identifier,
                                action="unmatched",
                                detail="not in this workspace's manifest",
                            ),
                        )
                    )
                    ordinal += 1
            for repo in repos:
                jobs.append(
                    RepoSyncJob(
                        ordinal=ordinal,
                        workspace=workspace,
                        manifest=manifest,
                        repo=repo,
                    )
                )
                ordinal += 1
            if prune:
                prune_targets.append(_PruneTarget(workspace=workspace, manifest=manifest))
        if unmatched_errors:
            raise UnmatchedRepoFilter(tuple(sorted(set(unmatched_errors))))
        return _SyncPlan(rows=rows, jobs=jobs, prune_targets=prune_targets)

    def _run_serial(
        self,
        jobs: Sequence[RepoSyncJob],
        tracker: BareFetchTracker,
    ) -> tuple[list[_PlannedOutcome], list[tuple[RepoSyncJob, Exception]]]:
        rows: list[_PlannedOutcome] = []
        unexpected: list[tuple[RepoSyncJob, Exception]] = []
        total = len(jobs)
        for done, job in enumerate(jobs, start=1):
            try:
                outcome = self._engine.sync_repo(job.workspace, job.manifest, job.repo, tracker)
            except Exception as exc:
                unexpected.append((job, exc))
            else:
                rows.append(
                    _PlannedOutcome(
                        ordinal=job.ordinal,
                        outcome=outcome,
                    )
                )
            self._notify_progress(done=done, total=total)
        return rows, unexpected

    def _run_parallel(
        self,
        jobs: Sequence[RepoSyncJob],
        tracker: BareFetchTracker,
        workers: int,
    ) -> tuple[list[_PlannedOutcome], list[tuple[RepoSyncJob, Exception]]]:
        rows: list[_PlannedOutcome] = []
        unexpected: list[tuple[RepoSyncJob, Exception]] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    self._engine.sync_repo,
                    job.workspace,
                    job.manifest,
                    job.repo,
                    tracker,
                ): job
                for job in jobs
            }
            total = len(futures)
            for done, fut in enumerate(as_completed(futures), start=1):
                job = futures[fut]
                try:
                    outcome = fut.result()
                except Exception as exc:
                    unexpected.append((job, exc))
                else:
                    rows.append(
                        _PlannedOutcome(
                            ordinal=job.ordinal,
                            outcome=outcome,
                        )
                    )
                self._notify_progress(done=done, total=total)
        return rows, unexpected

    def _notify_start(self, *, total: int, workers: int) -> None:
        if self._notify is not None:
            self._notify(f"syncing {total} repos with up to {workers} workers", new_phase=True)

    def _notify_progress(self, *, done: int, total: int) -> None:
        if self._notify is not None and total:
            self._notify(
                f"{done}/{total} repos complete",
                fraction=done / total,
            )


def _unexpected_sync_error(errors: Sequence[tuple[RepoSyncJob, Exception]]) -> WorkspaceError:
    details = [
        f"{job.workspace.name}/{job.repo.name}: {type(exc).__name__}: {exc}"
        for job, exc in errors[:3]
    ]
    if len(errors) > 3:
        details.append(f"{len(errors) - 3} more")
    return WorkspaceError(
        f"sync failed with unexpected error{'s' if len(errors) != 1 else ''}: " + "; ".join(details)
    )
