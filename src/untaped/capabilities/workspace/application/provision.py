"""Provision repos into a workspace: resolve, check out in parallel, record the successes."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.capabilities.workspace.application.locate import active_workspace, workspace_root
from untaped.capabilities.workspace.domain.models import (
    Checkout,
    RepoArg,
    RepoSpec,
    ResolvedRepo,
    WorkspaceRecord,
)
from untaped.capabilities.workspace.domain.naming import (
    assign_dirs,
    branch_for,
    repo_identity,
    repo_key,
    validate_workspace_name,
)
from untaped.capabilities.workspace.domain.records import RepoOutcome
from untaped.capabilities.workspace.errors import WorkspaceError
from untaped.capability_api import UntapedError, UsageError, bounded_map, note_failure, q

if TYPE_CHECKING:
    from untaped.capabilities.workspace.application.ports import (
        GitWorktrees,
        RepoCatalog,
        WorkspaceStore,
    )


@dataclass(frozen=True)
class _Job:
    """One repo to check out."""

    index: int
    arg: RepoArg
    resolved: ResolvedRepo
    dir: str
    branch: str | None


class ProvisionRepos:
    """The shared flow behind ``workspace create`` and ``workspace add``."""

    def __init__(
        self,
        store: WorkspaceStore,
        git: GitWorktrees,
        catalog: RepoCatalog,
        *,
        workspaces_dir: Path,
        branch_template: str,
        parallel: int,
        now: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._git = git
        self._catalog = catalog
        self._workspaces_dir = workspaces_dir
        self._branch_template = branch_template
        self._parallel = max(1, parallel)
        self._now = now

    def create(
        self,
        name: str,
        repos: Sequence[RepoArg],
        *,
        on_done: Callable[[RepoOutcome, int, int], None] | None = None,
    ) -> list[RepoOutcome]:
        """Create workspace ``name`` and check out ``repos`` into it.

        ``on_done(row, done, total)`` runs (on the calling thread) as each
        checkout finishes; ``total`` counts the checkouts started (repos
        already present or requested twice are not).
        """
        validate_workspace_name(name)
        if not repos:
            raise UsageError(
                "no repos given", hint=f"run `untaped workspace create {name} --repo REPO`"
            )
        resolved = self._resolve(repos)
        with self._store.locked(name):
            refuse_occupied(self._store, self._workspaces_dir, name)
            record = WorkspaceRecord(name=name, created_at=self._now())
            self._store.create(record)
            workspace_root(self._workspaces_dir, name).mkdir(parents=True, exist_ok=True)
            return self._provision(record, repos, resolved, on_done)

    def add(
        self,
        record: WorkspaceRecord,
        repos: Sequence[RepoArg],
        *,
        on_done: Callable[[RepoOutcome, int, int], None] | None = None,
    ) -> list[RepoOutcome]:
        """Check out ``repos`` into the existing workspace ``record``.

        The record is read again under the workspace lock: an ``archive`` that
        ran meanwhile makes this fail as not found instead of adding worktrees.
        ``on_done`` is as for :meth:`create`.
        """
        if not repos:
            raise UsageError(
                "no repos given", hint=f"run `untaped workspace add {record.name} --repo REPO`"
            )
        resolved = self._resolve(repos)
        with self._store.locked(record.name):
            current = active_workspace(self._store, record.name)
            workspace_root(self._workspaces_dir, current.name).mkdir(parents=True, exist_ok=True)
            return self._provision(current, repos, resolved, on_done)

    def _resolve(self, repos: Sequence[RepoArg]) -> list[ResolvedRepo]:
        return [self._resolve_one(arg) for arg in repos]

    def _resolve_one(self, arg: RepoArg) -> ResolvedRepo:
        """Resolve ``arg.ident``; when that fails, its ``fallback`` URL if it has one."""
        try:
            return self._catalog.resolve(arg.ident)
        except UntapedError:
            if arg.fallback is None:
                raise
            return self._catalog.resolve(arg.fallback)

    def _provision(
        self,
        record: WorkspaceRecord,
        repos: Sequence[RepoArg],
        resolved: Sequence[ResolvedRepo],
        on_done: Callable[[RepoOutcome, int, int], None] | None,
    ) -> list[RepoOutcome]:
        workspace_dir = workspace_root(self._workspaces_dir, record.name)
        rows, fresh = _partition(record, repos, resolved, workspace_dir)
        jobs = self._jobs(record, fresh)
        specs: dict[int, RepoSpec] = {}
        finished = 0

        def _run(job: _Job) -> Checkout | UntapedError:
            base = job.arg.base or job.resolved.default_branch
            try:
                return self._git.checkout(
                    job.resolved.url, workspace_dir / job.dir, branch=job.branch, base=base
                )
            except UntapedError as exc:
                return exc

        def _collect(job: _Job, result: Checkout | UntapedError) -> None:
            nonlocal finished
            rows[job.index] = self._row(record.name, workspace_dir, job, result)
            finished += 1
            if on_done is not None:
                on_done(rows[job.index], finished, len(jobs))
            if isinstance(result, Checkout):
                specs[job.index] = RepoSpec(
                    url=job.resolved.url,
                    name=job.resolved.name,
                    dir=job.dir,
                    branch=job.branch,
                    base=result.base,
                )

        try:
            bounded_map(_run, jobs, concurrency=self._parallel, on_each=_collect)
        finally:
            if specs:
                self._store.add_repos(record.name, [specs[i] for i in sorted(specs)])
        return [rows[i] for i in sorted(rows)]

    def _jobs(
        self, record: WorkspaceRecord, fresh: Sequence[tuple[int, RepoArg, ResolvedRepo]]
    ) -> list[_Job]:
        """One checkout job per new repo, with its directory and branch."""
        dirs = assign_dirs([repo_identity(repo.url) for _, _, repo in fresh], record.repos)
        return [
            _Job(index, arg, repo, dir_, self._branch(record.name, arg))
            for (index, arg, repo), dir_ in zip(fresh, dirs, strict=True)
        ]

    def _branch(self, workspace: str, arg: RepoArg) -> str | None:
        if arg.read_only:
            return None
        return arg.branch or branch_for(self._branch_template, workspace)

    @staticmethod
    def _row(
        workspace: str, workspace_dir: Path, job: _Job, result: Checkout | UntapedError
    ) -> RepoOutcome:
        common = {
            "workspace": workspace,
            "repo": job.resolved.name,
            "dir": job.dir,
            "branch": job.branch,
            "read_only": job.arg.read_only,
            "target_path": workspace_dir / job.dir,
        }
        if isinstance(result, Checkout):
            return RepoOutcome(
                **common, action=result.action, base=result.base, detail=result.detail
            )
        return RepoOutcome(
            **common,
            action="failed",
            base=job.arg.base or job.resolved.default_branch or "",
            detail=str(result),
            error=note_failure(result, message=str(result)),
        )


def refuse_occupied(store: WorkspaceStore, workspaces_dir: Path, name: str) -> None:
    """Refuse a new workspace whose directory already holds files (an old workspace?).

    An active workspace of that name is left for the store to report.
    """
    path = workspace_root(workspaces_dir, name)
    if store.get(name) is None and path.is_dir() and any(path.iterdir()):
        raise WorkspaceError(
            f"workspace directory {q(str(path))} already exists and is not empty",
            category="conflict",
            hint=(
                "a directory with that name already exists (perhaps an old workspace); "
                "move it aside or pick another name"
            ),
        )


def _partition(
    record: WorkspaceRecord,
    repos: Sequence[RepoArg],
    resolved: Sequence[ResolvedRepo],
    workspace_dir: Path,
) -> tuple[dict[int, RepoOutcome], list[tuple[int, RepoArg, ResolvedRepo]]]:
    """Split requested repos into ``unchanged`` rows (already present) and new ones.

    Both are keyed by request position; a repo requested twice counts once.
    """
    rows: dict[int, RepoOutcome] = {}
    present = {repo_key(spec.url): spec for spec in record.repos}
    fresh: list[tuple[int, RepoArg, ResolvedRepo]] = []
    seen: set[tuple[str, ...]] = set()
    for index, (arg, repo) in enumerate(zip(repos, resolved, strict=True)):
        key = repo_key(repo.url)
        if key in seen:
            continue
        seen.add(key)
        if (spec := present.get(key)) is not None:
            rows[index] = RepoOutcome(
                workspace=record.name,
                repo=spec.name,
                dir=spec.dir,
                action="unchanged",
                branch=spec.branch,
                base=spec.base,
                read_only=spec.read_only,
                target_path=workspace_dir / spec.dir,
            )
        else:
            fresh.append((index, arg, repo))
    return rows, fresh
