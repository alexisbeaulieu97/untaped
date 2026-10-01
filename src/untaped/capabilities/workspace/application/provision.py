"""Provision repos into a workspace: resolve, check out in parallel, record the successes."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

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
    validate_workspace_name,
)
from untaped.capabilities.workspace.domain.records import RepoOutcome
from untaped.capability_api import UntapedError, UsageError, bounded_map, note_failure

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

    def create(self, name: str, repos: Sequence[RepoArg]) -> list[RepoOutcome]:
        """Create workspace ``name`` and check out ``repos`` into it."""
        validate_workspace_name(name)
        if not repos:
            raise UsageError(
                "no repos given", hint=f"run `untaped workspace create {name} --repo REPO`"
            )
        resolved = self._resolve(repos)
        record = WorkspaceRecord(name=name, created_at=self._now())
        self._store.create(record)
        self._workspace_dir(name).mkdir(parents=True, exist_ok=True)
        return self._provision(record, repos, resolved)

    def add(self, record: WorkspaceRecord, repos: Sequence[RepoArg]) -> list[RepoOutcome]:
        """Check out ``repos`` into the existing workspace ``record``."""
        if not repos:
            raise UsageError(
                "no repos given", hint=f"run `untaped workspace add {record.name} --repo REPO`"
            )
        resolved = self._resolve(repos)
        self._workspace_dir(record.name).mkdir(parents=True, exist_ok=True)
        return self._provision(record, repos, resolved)

    def _workspace_dir(self, name: str) -> Path:
        return self._workspaces_dir.expanduser().absolute() / name

    def _resolve(self, repos: Sequence[RepoArg]) -> list[ResolvedRepo]:
        return [self._catalog.resolve(arg.ident) for arg in repos]

    def _provision(
        self, record: WorkspaceRecord, repos: Sequence[RepoArg], resolved: Sequence[ResolvedRepo]
    ) -> list[RepoOutcome]:
        workspace_dir = self._workspace_dir(record.name)
        rows: dict[int, RepoOutcome] = {}
        present = {spec.url: spec for spec in record.repos}
        fresh: list[tuple[int, RepoArg, ResolvedRepo]] = []
        seen: set[str] = set()
        for index, (arg, repo) in enumerate(zip(repos, resolved, strict=True)):
            if repo.url in seen:
                continue
            seen.add(repo.url)
            if (spec := present.get(repo.url)) is not None:
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
        dirs = assign_dirs([repo_identity(repo.url) for _, _, repo in fresh], record.repos)
        jobs = [
            _Job(index, arg, repo, dir_, self._branch(record.name, arg))
            for (index, arg, repo), dir_ in zip(fresh, dirs, strict=True)
        ]
        specs: dict[int, RepoSpec] = {}

        def _run(job: _Job) -> Checkout | UntapedError:
            base = job.arg.base or job.resolved.default_branch
            try:
                return self._git.checkout(
                    job.resolved.url, workspace_dir / job.dir, branch=job.branch, base=base
                )
            except UntapedError as exc:
                return exc

        def _collect(job: _Job, result: Checkout | UntapedError) -> None:
            rows[job.index] = self._row(record.name, workspace_dir, job, result)
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
