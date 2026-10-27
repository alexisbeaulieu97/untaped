"""Live git status of a workspace's repos, and what would block archiving each one."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import (
    NO_PROGRESS,
    Failure,
    ProgressHandle,
    UntapedError,
    bounded_map,
    command_line,
)
from untaped_workspace.application.locate import workspace_root
from untaped_workspace.domain.safety import NOT_STORED, UNREADABLE, archive_blockers
from untaped_workspace.records import RepoStatus

if TYPE_CHECKING:
    from untaped_workspace.application.ports import GitWorktrees
    from untaped_workspace.domain.models import RepoSpec, WorkspaceRecord


class WorkspaceStatus:
    """``workspace status``: one :class:`RepoStatus` per repo, offline unless ``fetch``.

    Repos are read ``parallel`` at a time; rows keep the record's order.

    A failed fetch does not stop the others: that repo's row still reports
    its local state, with ``detail`` and ``error`` saying why the fetch failed.
    A history backfill that stopped after a good fetch is no failure: the
    row's ``detail`` says so, and the next ``--fetch`` resumes it.
    A worktree git cannot read gets an ``error`` row that blocks archiving.
    """

    def __init__(self, git: GitWorktrees, *, workspaces_dir: Path, parallel: int) -> None:
        self._git = git
        self._workspaces_dir = workspaces_dir
        self._parallel = max(1, parallel)

    def __call__(
        self,
        record: WorkspaceRecord,
        *,
        fetch: bool = False,
        progress: ProgressHandle = NO_PROGRESS,
    ) -> list[RepoStatus]:
        """The rows of ``record``; with ``fetch``, ``progress`` hears the repos fetched.

        A workspace of one repo passes ``progress`` on to its fetch, whose
        phases are then the line; with more, the count of repos is.
        """
        root = workspace_root(self._workspaces_dir, record.name)
        rows: dict[int, RepoStatus] = {}
        inner = progress if len(record.repos) == 1 else NO_PROGRESS

        def _one(item: tuple[int, RepoSpec]) -> RepoStatus:
            spec = item[1]
            outcome = self._fetch(spec.url, root / spec.dir, inner) if fetch else None
            row = self._row(record.name, root, spec)
            if isinstance(outcome, Failure) and row.error is None:
                row = row.model_copy(
                    update={"detail": f"fetch failed: {outcome.message}", "error": outcome}
                )
            elif isinstance(outcome, str) and not row.detail:
                resume = resumes(record.name)
                row = row.model_copy(
                    update={"detail": f"history backfill stopped: {outcome}; {resume}"}
                )
            return row

        def _collect(item: tuple[int, RepoSpec], row: RepoStatus) -> None:
            rows[item[0]] = row
            if fetch:
                progress.update(phase=record.name, done=len(rows), total=len(record.repos))

        bounded_map(
            _one, list(enumerate(record.repos)), concurrency=self._parallel, on_each=_collect
        )
        return [rows[i] for i in sorted(rows)]

    def _fetch(self, url: str, dest: Path, progress: ProgressHandle) -> Failure | str | None:
        """Why the fetch failed, else why its history backfill stopped, else ``None``."""
        try:
            return self._git.fetch(url, dest, progress=progress)
        except UntapedError as exc:
            return Failure.from_exception(exc, message=str(exc))

    def _row(self, workspace: str, root: Path, spec: RepoSpec) -> RepoStatus:
        common = {
            "workspace": workspace,
            "repo": spec.name,
            "dir": spec.dir,
            "base": spec.base,
            "read_only": spec.read_only,
            "target_path": root / spec.dir,
        }
        if not self._git.in_store(spec.url):
            return RepoStatus(
                **common, branch=spec.branch, state="cache_missing", blockers=(NOT_STORED,)
            )
        try:
            status = self._git.status(root / spec.dir, branch=spec.branch, base=spec.base)
        except UntapedError as exc:
            reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            return RepoStatus(
                **common,
                branch=spec.branch,
                state="error",
                blockers=(f"{UNREADABLE}: {reason}",),
                detail=str(exc),
                error=Failure.from_exception(exc, message=str(exc)),
            )
        if status is None:
            return RepoStatus(**common, branch=spec.branch, state="missing")
        return RepoStatus(
            **common,
            branch=status.branch,
            state="ok",
            upstream=status.upstream,
            ahead=status.ahead,
            behind=status.behind,
            modified=status.modified,
            untracked=status.untracked,
            stashed=status.stashed,
            unpushed=status.unpushed,
            blockers=archive_blockers(status),
        )


def resumes(name: str) -> str:
    """What resumes workspace ``name``'s stopped history backfill."""
    from untaped_workspace.commands.status import Status  # noqa: PLC0415 - import cycle

    return f"`{command_line(Status.spelled('--fetch', name))}` resumes it"
