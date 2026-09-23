"""Use case: reconcile selected workspace repos through one global queue."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from untaped.capabilities.workspace.application.ports import ManifestReader
from untaped.capabilities.workspace.application.repo_selector import select_repos
from untaped.capabilities.workspace.application.sync_workspace import (
    BareFetchTracker,
    PruneCandidate,
    RepoSyncEngine,
)
from untaped.capabilities.workspace.domain import Repo, SyncOutcome, Workspace, WorkspaceManifest
from untaped.capabilities.workspace.errors import ManifestError, UnmatchedRepoFilter, WorkspaceError
from untaped.capability_api import bounded_map


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
class _SyncPlan:
    rows: list[_PlannedOutcome]
    jobs: list[RepoSyncJob]


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
        strict_only: bool = True,
        skip_manifest_errors: bool = False,
        parallel: int = 1,
        bare_tracker: BareFetchTracker | None = None,
    ) -> list[SyncOutcome]:
        tracker = bare_tracker if bare_tracker is not None else BareFetchTracker()
        plan = self._build_plan(
            workspaces,
            only=only,
            strict_only=strict_only,
            skip_manifest_errors=skip_manifest_errors,
        )
        planned: list[_PlannedOutcome] = list(plan.rows)
        if plan.jobs:
            workers = max(parallel, 1)
            if workers > 1 and len(plan.jobs) > 1:
                self._notify_start(total=len(plan.jobs), workers=workers)
            rows, unexpected = self._run_jobs(plan.jobs, tracker, workers)
            planned.extend(rows)
            if unexpected:
                raise _unexpected_sync_error(unexpected)

        return [row.outcome for row in sorted(planned, key=lambda row: row.ordinal)]

    def plan_prune(
        self,
        workspaces: Sequence[Workspace],
        *,
        skip_manifest_errors: bool = False,
    ) -> tuple[list[SyncOutcome], list[PruneCandidate]]:
        """Collect orphan ``skip`` rows and safe prune candidates, deleting nothing.

        Lets the CLI confirm destructive deletes (``sync --prune``) the
        same way ``remove --prune`` does. Workspaces whose manifest is
        unreadable are skipped under ``skip_manifest_errors`` (the sync
        phase already reported them as ``unavailable``).
        """
        rows: list[SyncOutcome] = []
        candidates: list[PruneCandidate] = []
        for workspace in workspaces:
            try:
                manifest = self._manifests.read(workspace.path)
            except ManifestError:
                if not skip_manifest_errors:
                    raise
                continue
            ws_rows, ws_candidates = self._engine.plan_prune(workspace, manifest)
            rows.extend(ws_rows)
            candidates.extend(ws_candidates)
        return rows, candidates

    def _build_plan(
        self,
        workspaces: Sequence[Workspace],
        *,
        only: Sequence[str] | None,
        strict_only: bool,
        skip_manifest_errors: bool,
    ) -> _SyncPlan:
        rows: list[_PlannedOutcome] = []
        jobs: list[RepoSyncJob] = []
        unmatched_errors: list[str] = []
        ordinal = 0
        for workspace in workspaces:
            try:
                manifest = self._manifests.read(workspace.path)
            except ManifestError as exc:
                if not skip_manifest_errors:
                    raise
                rows.append(
                    _PlannedOutcome(
                        ordinal=ordinal,
                        outcome=_unavailable_outcome(workspace, exc),
                    )
                )
                ordinal += 1
                continue
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
        if unmatched_errors:
            raise UnmatchedRepoFilter(tuple(sorted(set(unmatched_errors))))
        return _SyncPlan(rows=rows, jobs=jobs)

    def _run_jobs(
        self,
        jobs: Sequence[RepoSyncJob],
        tracker: BareFetchTracker,
        workers: int,
    ) -> tuple[list[_PlannedOutcome], list[tuple[RepoSyncJob, Exception]]]:
        """Run repo jobs on at most ``workers`` threads.

        Per-job exceptions are collected (the pool drains so every repo
        gets a row); anything escaping on the calling thread — including
        ``KeyboardInterrupt`` — cancels queued jobs via ``bounded_map``.
        """
        rows: list[_PlannedOutcome] = []
        unexpected: list[tuple[RepoSyncJob, Exception]] = []
        total = len(jobs)

        def _sync(job: RepoSyncJob) -> SyncOutcome | Exception:
            try:
                return self._engine.sync_repo(job.workspace, job.manifest, job.repo, tracker)
            except Exception as exc:
                return exc

        def _collect(job: RepoSyncJob, result: SyncOutcome | Exception) -> None:
            if isinstance(result, Exception):
                unexpected.append((job, result))
            else:
                rows.append(_PlannedOutcome(ordinal=job.ordinal, outcome=result))
            self._notify_progress(done=len(rows) + len(unexpected), total=total)

        bounded_map(_sync, jobs, concurrency=workers, on_each=_collect)
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


def _unavailable_outcome(workspace: Workspace, exc: ManifestError) -> SyncOutcome:
    return SyncOutcome(
        workspace=workspace.name,
        repo="",
        action="unavailable",
        detail=f"workspace manifest unavailable: {exc}",
    )
