"""Provision repos into a workspace: resolve, check out in parallel, record the successes.

Lock order: ``create``, ``add``, ``archive`` and ``remove`` take the
project's lock first, then a hold takes workspace's item store lock, then
each repo store call takes that repo's lock inside it. Never take them the
other way: a process holding a repo lock while it waits for a project's lock
can deadlock with one doing the opposite.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import Failure, UntapedError, UsageError, bounded_map, q
from untaped.sdk.storage import NO_PROGRESS
from untaped_workspace.application.locate import workspace_root
from untaped_workspace.application.status import resumes
from untaped_workspace.domain.models import (
    Checkout,
    RepoArg,
    RepoSpec,
    WorkspaceRecord,
)
from untaped_workspace.domain.naming import (
    assign_dirs,
    branch_for,
    repo_identity,
    repo_key,
    validate_workspace_name,
)
from untaped_workspace.domain.repo import Repo
from untaped_workspace.errors import WorkspaceError
from untaped_workspace.records import RepoOutcome

if TYPE_CHECKING:
    from untaped.sdk.storage import ProgressHandle
    from untaped_workspace.application.ports import (
        GitWorktrees,
        RepoCatalog,
        WorkspaceStore,
    )


@dataclass(frozen=True)
class _Job:
    """One repo to check out."""

    index: int
    arg: RepoArg
    resolved: Repo
    dir: str
    branch: str | None


class ProvisionRepos:
    """The shared flow behind ``workspace create`` and ``workspace add``.

    ``warn`` gets one line per checkout whose history backfill stopped: the
    worktree is there and usable, so it is no failure.
    """

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
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self._store = store
        self._git = git
        self._catalog = catalog
        self._workspaces_dir = workspaces_dir
        self._branch_template = branch_template
        self._parallel = max(1, parallel)
        self._now = now
        self._warn = warn

    def create(
        self,
        name: str,
        repos: Sequence[RepoArg],
        *,
        on_done: Callable[[RepoOutcome, int, int], None] | None = None,
        progress: ProgressHandle = NO_PROGRESS,
    ) -> list[RepoOutcome]:
        """Create workspace ``name`` and check out ``repos`` into it (none: an empty workspace).

        The workspace is the project ``<workspaces_dir>/NAME``, made as
        ``untaped project init`` makes one. An archived workspace there is
        resumed: its repos are checked out again, then ``repos``.
        ``on_done(row, done, total)`` runs (on the calling thread) as each
        checkout finishes; ``total`` counts the checkouts started (repos
        already present or requested twice are not). ``progress`` counts the
        repo sources asked while names are resolved.
        """
        name = validate_workspace_name(name)
        resolved = self._resolve(repos, progress)
        root = workspace_root(self._workspaces_dir, name)
        existing = self._store.at(root)
        if existing is not None and existing.meta is not None and not existing.archived:
            from untaped_workspace.commands.create import Add  # noqa: PLC0415 - import cycle

            raise WorkspaceError(
                f"workspace {q(name)} already exists",
                category="conflict",
                hint=Add.spelled(name, "--repo", "REPO"),
            )
        if existing is None:
            refuse_occupied(root)
            existing = self._store.start(root, name, at=self._now())
        return self._into(existing, repos, resolved, on_done)

    def add(
        self,
        record: WorkspaceRecord,
        repos: Sequence[RepoArg],
        *,
        on_done: Callable[[RepoOutcome, int, int], None] | None = None,
        progress: ProgressHandle = NO_PROGRESS,
    ) -> list[RepoOutcome]:
        """Check out ``repos`` into the workspace ``record`` (an archived one resumes first).

        Any project takes repos: the first ``add`` makes it a workspace. The
        record is read again under the project's lock. ``on_done`` and
        ``progress`` are as for :meth:`create`.
        """
        if not repos and not record.archived:
            from untaped_workspace.commands.create import Add  # noqa: PLC0415 - import cycle

            raise UsageError("no repos given", hint=Add.spelled(record.name, "--repo", "REPO"))
        resolved = self._resolve(repos, progress)
        return self._into(record, repos, resolved, on_done)

    def _into(
        self,
        record: WorkspaceRecord,
        repos: Sequence[RepoArg],
        resolved: Sequence[Repo],
        on_done: Callable[[RepoOutcome, int, int], None] | None,
    ) -> list[RepoOutcome]:
        """Under the project's lock: make it a workspace, resume it if archived, check out."""
        with self._store.locked(record) as current:
            current = self._store.adopt(current, at=self._now())
            if current.archived:  # its recorded repos are checked out again, then the new ones
                recorded = [_again(spec) for spec in current.repos]
                repos = [*recorded, *repos]
                resolved = [*(arg.repo for arg in recorded if arg.repo is not None), *resolved]
                current = self._store.set_archived(current, None)
            current.root.mkdir(parents=True, exist_ok=True)
            return self._provision(current, repos, resolved, on_done)

    def _resolve(self, repos: Sequence[RepoArg], progress: ProgressHandle) -> list[Repo]:
        """Each argument's repo: the one already chosen (picked, piped), else ``ident`` resolved."""
        return [
            self._catalog.admit(arg.repo)
            if arg.repo is not None
            else self._catalog.resolve(arg.ident, progress=progress)
            for arg in repos
        ]

    def _provision(
        self,
        record: WorkspaceRecord,
        repos: Sequence[RepoArg],
        resolved: Sequence[Repo],
        on_done: Callable[[RepoOutcome, int, int], None] | None,
    ) -> list[RepoOutcome]:
        workspace_dir = record.root
        rows, fresh, again = _partition(record, repos, resolved, workspace_dir)
        jobs = [*self._jobs(record, fresh), *again]
        specs: dict[int, RepoSpec] = {}
        finished = 0
        lock = f"untaped project {record.project.id}"
        for job in jobs:  # hold, then use: on this thread, which holds the project's lock
            self._store.hold(record, job.resolved.url)

        def _run(job: _Job) -> Checkout | UntapedError:
            base = job.arg.base or job.resolved.default_branch
            try:
                return self._git.checkout(
                    job.resolved.url,
                    workspace_dir / job.dir,
                    branch=job.branch,
                    base=base,
                    lock=lock,
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
                if result.backfill_error is not None and self._warn is not None:
                    self._warn(
                        f"history backfill of {job.resolved.name} stopped: "
                        f"{result.backfill_error}; {resumes(record.name)}"
                    )
                specs[job.index] = RepoSpec(
                    **dict(job.resolved),
                    dir=job.dir,
                    branch=job.branch,
                    base=result.base,
                )

        try:
            bounded_map(_run, jobs, concurrency=self._parallel, on_each=_collect)
        finally:
            if specs:
                self._store.put_repos(record, [specs[i] for i in sorted(specs)])
        return [rows[i] for i in sorted(rows)]

    def _jobs(
        self, record: WorkspaceRecord, fresh: Sequence[tuple[int, RepoArg, Repo]]
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
            error=Failure.from_exception(result, message=str(result)),
        )


def refuse_occupied(path: Path) -> None:
    """Refuse a new workspace whose folder already holds files and is no project."""
    if path.is_dir() and any(path.iterdir()):
        raise WorkspaceError(
            f"workspace directory {q(str(path))} already exists and is not empty",
            category="conflict",
            hint=(
                "a directory with that name already exists; move it aside or pick another "
                f"name, or make it a project (`untaped project init {path}`) and add repos to it"
            ),
        )


def _again(spec: RepoSpec) -> RepoArg:
    """A recorded repo, to check out again where it was (a resumed workspace)."""
    repo = Repo.model_construct(**{name: getattr(spec, name) for name in Repo.model_fields})
    return RepoArg(
        ident=spec.name, read_only=spec.read_only, branch=spec.branch, base=spec.base, repo=repo
    )


def _partition(
    record: WorkspaceRecord,
    repos: Sequence[RepoArg],
    resolved: Sequence[Repo],
    workspace_dir: Path,
) -> tuple[dict[int, RepoOutcome], list[tuple[int, RepoArg, Repo]], list[_Job]]:
    """Split requested repos into ``unchanged`` rows, new repos, and recorded ones to check out
    again (their worktree is gone: a resumed workspace, a folder deleted by hand).

    All are keyed by request position; a repo requested twice counts once.
    """
    rows: dict[int, RepoOutcome] = {}
    present = {repo_key(spec.url): spec for spec in record.repos}
    fresh: list[tuple[int, RepoArg, Repo]] = []
    again: list[_Job] = []
    seen: set[tuple[str, ...]] = set()
    for index, (arg, repo) in enumerate(zip(repos, resolved, strict=True)):
        key = repo_key(repo.url)
        if key in seen:
            continue
        seen.add(key)
        spec = present.get(key)
        if spec is None:
            fresh.append((index, arg, repo))
        elif not (workspace_dir / spec.dir).exists():
            base = arg.base or spec.base
            again.append(
                _Job(index, arg.model_copy(update={"base": base}), repo, spec.dir, spec.branch)
            )
        else:
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
    return rows, fresh, again
