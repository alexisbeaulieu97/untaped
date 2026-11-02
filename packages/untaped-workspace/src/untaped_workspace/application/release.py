"""How a project lets go of a repo it holds through workspace, and repairs a move.

Core's holds service (:mod:`untaped.sdk.storage.holds`) asks workspace, the
owner of ``workspace.repos``, whenever a project lets go of a repo: ``untaped
project remove``, ``project prune``, or ``workspace remove``. Workspace
answers for its part; the repo store keeps deciding, from the repo itself,
whether anything else keeps the repo (another plugin's refs, a hand-made
worktree, a branch, a stash).

- **Work in the project's worktree** (uncommitted, stashed or unpushed, as
  ``archive`` counts it) refuses, unless forced.
- **Another project holds the repo:** only this project's worktree goes; the
  repo and its branches stay. If the other project let go since the preview,
  the apply refuses rather than delete more than it said.
- **The last holder:** the worktree goes, then the repo is released: the
  project's branch with commits no remote has refuses unless forced (a
  missing project's rows can't be read, so then any such branch does);
  other branches with work on them stay and keep the repo, unless forced; a
  stash is never deleted.

A project that moved gets its worktrees reconnected (:func:`repair_moved`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from untaped.sdk import (
    LetGo,
    MoveAsk,
    Project,
    Refused,
    ReleaseAsk,
    UntapedError,
    project_at,
    shown_path,
)
from untaped_workspace.domain.models import RepoKey, RepoSpec
from untaped_workspace.domain.naming import store_ident
from untaped_workspace.domain.safety import (
    archive_blockers,
    branch_work,
    releasable_branches,
    unpushed_branch_blocker,
)
from untaped_workspace.errors import GitError
from untaped_workspace.infrastructure.git_worktrees import LocalGitWorktrees

#: The plugin the repo store keeps workspace's refs under.
PLUGIN = "workspace"


@dataclass(frozen=True)
class _Held:
    """What workspace knows of a held repo: the project's row (if readable) and the URL."""

    project: Project | None
    row: RepoSpec | None
    url: str | None
    discards: tuple[str, ...] = field(default=())


def plan_release(ask: ReleaseAsk[RepoKey]) -> LetGo[RepoKey] | Refused:
    """Workspace's answer when a project lets go of ``ask.item``; see the module."""
    git = LocalGitWorktrees(plugin=PLUGIN)
    held = _held(ask, git)
    where = shown_path(ask.holder.anchor)
    if held.row is not None and held.project is not None and not ask.force:
        work = _worktree_work(git, held.project.root / held.row.dir, held.row)
        if work:
            return Refused(
                reason=f"worktree {held.row.dir}: {', '.join(work)}",
                hint=f"commit and push it from {shown_path(held.project.root / held.row.dir)}",
            )
    folder = held.row.dir if held.row is not None else "its worktree"
    if held.url is None:
        return LetGo(preview="not in the repo store; forgets the hold", apply=_nothing)
    if ask.others:
        return LetGo(
            preview=f"removes {folder}; keeps the repo: {ask.others[0].label} holds it",
            apply=lambda fresh: _keep_repo(fresh, held.url or ""),
        )
    branches = _branch_blockers(git, held)
    if branches.blockers and not ask.force:
        return Refused(
            reason="; ".join(branches.blockers),
            hint=f"push it from a worktree on it (in {where} or another workspace)",
        )
    preview = f"removes {folder}; releases the repo from the repo store"
    if ask.force and branches.discards:
        preview += f"; deletes {'; '.join(branches.discards)}"
    return LetGo(preview=preview, apply=lambda fresh: _release_repo(fresh, held.url or "", git))


def repair_moved(ask: MoveAsk[RepoKey]) -> str | None:
    """Reconnect the moved project's worktree of ``ask.item`` (``git worktree repair``)."""
    git = LocalGitWorktrees(plugin=PLUGIN)
    project = project_at(ask.holder.anchor)
    if project is None:
        return None
    from untaped_workspace.infrastructure.project_store import WORKTREES  # noqa: PLC0415

    ident = ask.item.root
    for row in WORKTREES.of(project).all():
        if store_ident(row.url) == ident and git.repair(row.url, project.root / row.dir):
            return f"reconnected worktree {row.dir}"
    return None


# -- the steps ---------------------------------------------------------------


def _nothing(_fresh: ReleaseAsk[RepoKey]) -> str:
    return "forgot the hold: the repo store has no repo for it"


def _keep_repo(fresh: ReleaseAsk[RepoKey], url: str) -> str | Refused:
    if not fresh.others:  # the other holder let go since the preview: deleting is more than it said
        return Refused(reason="now the last holder", hint="run the removal again to release it")
    dropped = _drop(fresh, url, LocalGitWorktrees(plugin=PLUGIN))
    if isinstance(dropped, Refused):
        return dropped
    kept = f"kept the repo for {fresh.others[0].label}"
    return f"removed {_count(dropped)}; {kept}" if dropped else kept


def _release_repo(fresh: ReleaseAsk[RepoKey], url: str, git: LocalGitWorktrees) -> str | Refused:
    dropped = _drop(fresh, url, git)
    if isinstance(dropped, Refused):
        return dropped
    done = f"removed {_count(dropped)}; " if dropped else ""
    if fresh.others:  # another holder came since the preview: keep, which is less than it said
        return f"{done}kept the repo for {fresh.others[0].label}"
    use = git.store_use(url, bases=())
    if use is None:
        return f"{done}the repo store has no repo for it"
    released = git.release(url, branches=releasable_branches(use.branches, force=fresh.force))
    return done + _released_text(released.action, released.detail)


def _drop(fresh: ReleaseAsk[RepoKey], url: str, git: LocalGitWorktrees) -> list[Path] | Refused:
    """Remove the project's worktree of the repo: the one its row names, checked again first.

    Work that appeared since the preview refuses unless forced (the holds
    service lets an apply do less than its preview, never more). A project
    not here has no row to read: every entry under its folder goes.
    """
    held = _held(fresh, git)
    checked = (
        {held.project.root / held.row.dir: held.row.base}
        if held.project is not None and held.row is not None
        else None
    )
    try:
        return git.drop_worktrees(url, fresh.holder.anchor, checked=checked, force=fresh.force)
    except GitError as exc:
        if exc.category != "conflict":
            raise
        return Refused(reason=str(exc), hint=exc.hint or "run status, then the removal again")


def _released_text(action: str, detail: str) -> str:
    """What the repo store did with the repo, as a clause."""
    if action == "removed":
        return f"removed the repo from the repo store ({detail})"
    if action == "kept":
        return f"kept the repo: {detail}"
    return f"released the repo; the store keeps it for {detail.removeprefix('kept: ')}"


# -- reading what is held ------------------------------------------------------


def _held(ask: ReleaseAsk[RepoKey], git: LocalGitWorktrees) -> _Held:
    """The holder's project and row for the item when its folder is here, and the repo's URL."""
    ident = ask.item.root
    project = project_at(ask.holder.anchor) if ask.holder.state == "present" else None
    row = None
    if project is not None:
        from untaped_workspace.infrastructure.project_store import WORKTREES  # noqa: PLC0415

        row = next(
            (spec for spec in WORKTREES.of(project).all() if store_ident(spec.url) == ident), None
        )
    url = row.url if row is not None and git.in_store(row.url) else git.stored_url(ident)
    return _Held(project=project, row=row, url=url)


def _worktree_work(git: LocalGitWorktrees, dest: Path, row: RepoSpec) -> tuple[str, ...]:
    """What removing the worktree at ``dest`` would lose (``archive``'s blockers)."""
    try:
        return archive_blockers(git.status(dest, branch=row.branch, base=row.base))
    except UntapedError as exc:
        return (f"git state unreadable: {str(exc).splitlines()[0] if str(exc) else exc}",)


@dataclass(frozen=True)
class _Branches:
    blockers: tuple[str, ...]
    discards: tuple[str, ...]


def _branch_blockers(git: LocalGitWorktrees, held: _Held) -> _Branches:
    """The project's branch with unpushed commits (any branch, without its row), and other work."""
    assert held.url is not None
    bases = frozenset({held.row.base}) if held.row is not None else frozenset()
    use = git.store_use(held.url, bases=bases)
    if use is None:
        return _Branches((), ())
    own = held.row.branch if held.row is not None else None
    blockers, discards = [], []
    for branch in use.branches:
        if branch.checked_out and branch.name != own:
            continue  # another worktree's (a hand-made one): the store keeps the repo for it
        reason = unpushed_branch_blocker(branch)
        if reason is not None and (own is None or branch.name == own):
            blockers.append(reason)
        elif (work := branch_work(branch)) is not None:
            discards.append(work)
    return _Branches(tuple(blockers), tuple(discards))


def _count(dropped: list[Path]) -> str:
    if len(dropped) == 1:
        return f"worktree {dropped[0].name}"
    return f"{len(dropped)} worktrees"
