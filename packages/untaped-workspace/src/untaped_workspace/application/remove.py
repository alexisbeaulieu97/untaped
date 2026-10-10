"""Remove a workspace: drop its records, then release each repo no other workspace names.

``archive`` is reversible: the record and the local branches stay, so a later
``create`` on the branch resumes offline. ``remove`` is the verb that gives
the space back. It archives an active workspace first (with archive's checks),
drops every record of the name, active and archived, and then, for each repo
no remaining record names, asks the repo store to release it: workspace's
refs, file and worktrees go, and the repo itself when no other plugin holds
anything in it.

Release deletes local branches only by the rule archive already applies to
work (:func:`~untaped_workspace.domain.safety.releasable_branches`): a branch
another worktree has checked out, one with unpushed commits or with a stash
made on it stays, and keeps the repo. ``--force`` lets every branch go but a
checked-out one; a stash (``refs/stash``) is never deleted, though the branch
it was made on is. Before anything changes, a workspace whose own branch has
commits the remote lacks is refused unless forced, because release would
delete that branch; any other branch ``--force`` would delete with work on it
is named in the preview and the confirmation.

The caller holds the workspace lock from the plan through the removal. A
``create`` of the same repo in another workspace can still start meanwhile:
the store checks under its repo lock that no workspace worktree is registered
before it deletes anything, so a repo that ``create`` has added its worktree
to is kept. A ``create`` caught between its fetch and its worktree fails
instead (nothing is lost; running it again fetches the repo anew).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import UntapedError, not_found, note_failure, plural
from untaped_workspace.application.archive import ArchiveWorkspace
from untaped_workspace.application.locate import workspace_root
from untaped_workspace.domain.naming import repo_key
from untaped_workspace.domain.records import RemoveOutcome
from untaped_workspace.domain.safety import (
    archive_hint,
    branch_work,
    releasable_branches,
    unpushed_branch_blocker,
)
from untaped_workspace.errors import WorkspaceNotFoundError

if TYPE_CHECKING:
    from untaped_workspace.application.ports import GitWorktrees, WorkspaceStore
    from untaped_workspace.application.status import WorkspaceStatus
    from untaped_workspace.domain.models import (
        ArchivedRecord,
        RepoSpec,
        WorkspaceRecord,
    )
    from untaped_workspace.domain.records import StatusRow

type Key = tuple[str, ...]


@dataclass(frozen=True)
class RepoPlan:
    """One repo of the removal: who else holds it, and what blocks releasing it."""

    spec: RepoSpec
    held_by: str | None
    """Another workspace naming the repo (it is then kept, not released)."""
    stored: bool
    blockers: tuple[str, ...]
    """Work release would lose: the active worktree's blockers, unpushed branches."""
    discards: tuple[str, ...] = ()
    """Other branches with work on them that only ``--force`` deletes (no record names them)."""


@dataclass(frozen=True)
class RemovalPlan:
    """What ``remove NAME`` would do, from the records and the repo store as they are now."""

    name: str
    active: WorkspaceRecord | None
    archived: tuple[ArchivedRecord, ...]
    repos: tuple[RepoPlan, ...]
    status: tuple[StatusRow, ...]
    """The active workspace's status rows (archive's check); empty when it is archived."""

    @property
    def blocked(self) -> list[RepoPlan]:
        return [repo for repo in self.repos if repo.blockers]


class RemoveWorkspace:
    """``workspace remove``: plan under the workspace lock, then apply (see the module)."""

    def __init__(
        self,
        store: WorkspaceStore,
        git: GitWorktrees,
        *,
        status: WorkspaceStatus,
        workspaces_dir: Path,
        now: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._git = git
        self._status = status
        self._workspaces_dir = workspaces_dir
        self._archive = ArchiveWorkspace(store, git, workspaces_dir=workspaces_dir, now=now)

    @contextmanager
    def hold(self, name: str) -> Iterator[RemovalPlan]:
        """Hold workspace ``name``'s lock; yield the plan for removing it.

        Not found when no record, active or archived, has the name.
        """
        with self._store.locked(name):
            yield self._plan(name)

    def __call__(self, plan: RemovalPlan, *, force: bool) -> list[RemoveOutcome]:
        """Remove ``plan``'s workspace (the caller holds :meth:`hold`).

        An active workspace is archived first; when a worktree fails to go,
        its row is ``failed`` and every record stays.
        """
        rows: list[RemoveOutcome] = []
        root = workspace_root(self._workspaces_dir, plan.name)
        detail = "workspace record"
        if plan.active is not None:
            archived = self._archive(plan.active, force=force)
            failed = [row for row in archived if row.action == "failed"]
            if failed:
                return [
                    RemoveOutcome(
                        workspace=row.workspace,
                        repo=row.repo,
                        action="failed",
                        detail=row.detail,
                        target_path=row.target_path,
                        error=row.error,
                    )
                    for row in failed
                ]
            directory = archived[-1]
            detail = (
                f"workspace record; {directory.detail}"
                if directory.action == "skipped"
                else "workspace record and directory"
            )
        self._store.remove(plan.name)
        holders = self._holders()
        for repo in plan.repos:
            rows.append(self._release(plan.name, root, repo, holders, force=force))
        rows.append(
            RemoveOutcome(
                workspace=plan.name, repo="", action="removed", detail=detail, target_path=root
            )
        )
        return rows

    def preview(self, plan: RemovalPlan, *, force: bool) -> list[RemoveOutcome]:
        """What :meth:`__call__` would do: ``planned`` rows, ``skipped`` for blocked repos."""
        root = workspace_root(self._workspaces_dir, plan.name)
        rows = []
        for repo in plan.repos:
            blocked = bool(repo.blockers) and not force
            if repo.held_by is not None:
                detail = f"kept: used by workspace {repo.held_by}"
            elif not repo.stored:
                detail = "not in the repo store"
            else:
                detail = "release from the repo store"
            if repo.blockers:
                detail = f"{'; '.join(repo.blockers)}; {detail}"
            if force and repo.discards:
                detail = f"{detail}; deletes {'; '.join(repo.discards)}"
            rows.append(
                RemoveOutcome(
                    workspace=plan.name,
                    repo=repo.spec.name,
                    action="skipped" if blocked else "planned",
                    detail=detail,
                    target_path=root / repo.spec.dir,
                )
            )
        rows.append(
            RemoveOutcome(
                workspace=plan.name,
                repo="",
                action="planned",
                detail=(
                    "archive, then drop the workspace record"
                    if plan.active is not None
                    else "drop the workspace record"
                ),
                target_path=root,
            )
        )
        return rows

    # -- planning ----------------------------------------------------------

    def _plan(self, name: str) -> RemovalPlan:
        active = self._store.get(name)
        archived = tuple(record for record in self._store.archived() if record.name == name)
        if active is None and not archived:
            known = sorted({r.name for r in (*self._store.active(), *self._store.archived())})
            raise WorkspaceNotFoundError(not_found("workspace", name, known=known))
        status = tuple(self._status(active)) if active is not None else ()
        holders = self._holders(excluding=name)
        specs: dict[Key, RepoSpec] = {}
        blockers: dict[Key, list[str]] = {}
        for row, spec in zip(status, active.repos if active is not None else (), strict=True):
            key = repo_key(spec.url)
            specs[key] = spec
            blockers[key] = list(row.blockers)
        for record in archived:
            for spec in record.repos:
                specs.setdefault(repo_key(spec.url), spec)
        branches: dict[Key, set[str]] = {}
        for record in archived:  # an active branch's commits are in its status row already
            for spec in record.repos:
                if spec.branch is not None:
                    branches.setdefault(repo_key(spec.url), set()).add(spec.branch)
        repos = []
        for key, spec in specs.items():
            held_by = holders.get(key)
            use = self._git.store_use(spec.url) if held_by is None else None
            found = blockers.setdefault(key, [])
            discards = []
            for branch in use.branches if use is not None else ():
                reason = unpushed_branch_blocker(branch)
                if branch.name in branches.get(key, ()) and reason is not None:
                    found.append(reason)
                elif not branch.checked_out and (work := branch_work(branch)) is not None:
                    discards.append(work)
            repos.append(
                RepoPlan(
                    spec=spec,
                    held_by=held_by,
                    stored=held_by is not None or use is not None,
                    blockers=tuple(found),
                    discards=tuple(discards),
                )
            )
        return RemovalPlan(
            name=name, active=active, archived=archived, repos=tuple(repos), status=status
        )

    def _holders(self, excluding: str | None = None) -> dict[Key, str]:
        """Each repo a remaining record names → the first workspace naming it."""
        holders: dict[Key, str] = {}
        for record in (*self._store.active(), *self._store.archived()):
            if record.name == excluding:
                continue
            for spec in record.repos:
                holders.setdefault(repo_key(spec.url), record.name)
        return holders

    # -- applying ----------------------------------------------------------

    def _release(
        self, name: str, root: Path, repo: RepoPlan, holders: dict[Key, str], *, force: bool
    ) -> RemoveOutcome:
        """Release one repo, unless another record names it or a workspace worktree uses it."""
        spec = repo.spec
        common = {"workspace": name, "repo": spec.name, "target_path": root / spec.dir}
        holder = holders.get(repo_key(spec.url))
        if holder is not None:
            return RemoveOutcome(**common, action="kept", detail=f"used by workspace {holder}")
        try:
            use = self._git.store_use(spec.url)
            if use is None:
                return RemoveOutcome(**common, action="skipped", detail="not in the repo store")
            if use.worktrees:
                return RemoveOutcome(
                    **common,
                    action="kept",
                    detail=f"used by {plural(use.worktrees, 'workspace worktree')}",
                )
            branches = releasable_branches(use.branches, force=force)
            released = self._git.release(spec.url, branches=branches)
        except UntapedError as exc:
            return RemoveOutcome(
                **common,
                action="failed",
                detail=str(exc),
                error=note_failure(exc, message=str(exc)),
            )
        return RemoveOutcome(
            **common,
            action=released.action,
            detail=released.detail,
            freed_bytes=released.freed_bytes,
        )


def refusal_hint(plan: RemovalPlan) -> str:
    """A non-destructive next step for what blocks ``plan``."""
    parts = []
    blocked_rows = [row for row in plan.status if row.blockers]
    if blocked_rows:
        parts.append(archive_hint(blocked_rows))
    if any(blocker.startswith("branch ") for repo in plan.blocked for blocker in repo.blockers):
        parts.append(
            "push each unpushed branch from a workspace on it "
            "(`untaped workspace create NAME --repo REPO --branch BRANCH`), "
            "or pass --force to delete it"
        )
    return "; ".join(parts)
