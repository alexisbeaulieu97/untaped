"""Workspaces as projects: the rows each project keeps, the repos it holds, and finding them.

A workspace is a project (:mod:`untaped.sdk.projects`) with workspace's meta
row in its ``.untaped/``. The plugin class holds the blocks declared here:

- ``checkouts``: the ``workspaces_dir`` folder, where ``create`` makes projects;
- ``worktrees`` and ``meta``: the rows each project keeps (one per repo, and one
  saying it is a workspace);
- ``repos``: the item store of repos projects hold through workspace, released
  through :func:`~untaped_workspace.application.release.plan_release`.

The project's lock serialises ``create``, ``add``, ``archive`` and ``remove``
of one workspace; each repo store call takes that repo's lock inside it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from untaped.sdk import (
    Dir,
    HolderView,
    LetGo,
    MoveAsk,
    Project,
    ProjectRemoval,
    Refused,
    ReleaseAsk,
    StateRows,
    UsageError,
    current_project,
    init_project,
    key,
    known_projects,
    not_found,
    project_at,
    q,
    remove_project,
    shown_path,
)
from untaped_workspace.domain.models import RepoKey, RepoSpec, WorkspaceMeta, WorkspaceRecord
from untaped_workspace.domain.naming import store_ident, validate_workspace_name, workspace_key
from untaped_workspace.errors import WorkspaceError, WorkspaceNotFoundError
from untaped_workspace.settings import WorkspaceSettings

if TYPE_CHECKING:
    from untaped.sdk import OutcomeRecord


def _release(ask: ReleaseAsk[RepoKey]) -> LetGo[RepoKey] | Refused:
    from untaped_workspace.application.release import plan_release  # noqa: PLC0415 - git loads late

    return plan_release(ask)


def _moved(ask: MoveAsk[RepoKey]) -> str | None:
    from untaped_workspace.application.release import repair_moved  # noqa: PLC0415 - git loads late

    return repair_moved(ask)


#: Every workspace ``create`` makes: ``<workspaces_dir>/NAME``, each a project.
CHECKOUTS = Dir(at=key(WorkspaceSettings).workspaces_dir)
#: The repos projects hold through workspace, by repo store key; see the module.
HELD_REPOS = Dir(items=RepoKey, release=_release, moved=_moved)
#: Each project's repos, one row per worktree folder.
WORKTREES = StateRows[RepoSpec].per_project()
#: The row that makes a project a workspace (one per project).
META = StateRows[WorkspaceMeta].per_project()


def copy_refusal(record: WorkspaceRecord) -> WorkspaceError | None:
    """Why workspace runs no git in ``record``'s folder: it is a copy (design §1.6.7).

    Before its first writing use a copy still has the original's id
    (``copy_of`` is set); after it, its meta row names the original's id.
    Even ``git status`` in a copied worktree refreshes the original's index.
    A row whose repo the project no longer holds (a removal that let go of
    some repos and not others) is no copy: ``remove`` drops it.
    """
    project = record.project
    moved_in = record.meta is not None and record.meta.project != project.id
    if project.copy_of is None and not moved_in:
        return None
    dirs = [spec.dir for spec in record.repos]
    original = shown_path(project.copy_of) if project.copy_of is not None else "another project"
    here = " and ".join(f"{name}/" for name in dirs) or "its worktrees"
    return WorkspaceError(
        f"repos in this copy belong to {original}: delete {here} here, then "
        "`untaped workspace add --repo REPO`",
        category="conflict",
        hint="or work in the original folder",
    )


def record_of(project: Project) -> WorkspaceRecord:
    """``project`` as a workspace: its meta row (``None`` before its first ``add``), its repos."""
    metas = META.of(project).all()
    return WorkspaceRecord(
        project=project,
        meta=metas[0] if metas else None,
        repos=tuple(WORKTREES.of(project).all()),
    )


class ProjectWorkspaceStore:
    """Workspace records kept in projects; see the module."""

    @contextmanager
    def locked(self, record: WorkspaceRecord) -> Iterator[WorkspaceRecord]:
        """Hold the workspace's project lock; yield the record as stored now."""
        with record.project.locked():
            yield record_of(record.project)

    def all(self) -> list[WorkspaceRecord]:
        """Every workspace whose project is here now, active and archived, by name."""
        found: list[WorkspaceRecord] = []
        for seen in known_projects():
            if not isinstance(seen, HolderView) or seen.state != "present":
                continue
            project = project_at(seen.anchor)
            if project is None:
                continue
            record = record_of(project)
            if record.meta is not None and copy_refusal(record) is None:  # a copy isn't listed
                found.append(record)
        return sorted(found, key=lambda record: (record.name, str(record.root)))

    def current(self) -> WorkspaceRecord:
        """The workspace of the project the working directory is in (exit 2 outside one)."""
        return _usable(record_of(current_project()))

    def at(self, root: Path) -> WorkspaceRecord | None:
        """The workspace of the project at ``root`` exactly, or ``None``."""
        project = project_at(root)
        if project is None or project.root != root.expanduser().resolve():
            return None
        return _usable(record_of(project))

    def find(self, name: str) -> WorkspaceRecord:
        """The workspace ``name`` names: a path to a project, else a workspace's name.

        One active workspace of that name, else the newest archived one;
        two active ones are ambiguous (exit 2, listing both folders).
        """
        if _is_path(name):
            path = Path(name).expanduser()
            project = project_at(path)
            if project is None:
                raise UsageError(f"{shown_path(path.resolve())} is in no project")
            return _usable(record_of(project))
        wanted = workspace_key(name)
        every = self.all()
        named = [record for record in every if record.name == wanted]
        active = [record for record in named if not record.archived]
        _refuse_ambiguous(wanted, active)
        if active:
            return _usable(active[0])
        if named:
            return _usable(max(named, key=lambda record: _archived_at(record)))
        known = sorted({record.name for record in every})
        raise WorkspaceNotFoundError(not_found("workspace", wanted, known=known))

    def named(self, name: str) -> list[WorkspaceRecord]:
        """Every workspace of ``name``, active and archived (or the one a path names).

        Two active ones are ambiguous (exit 2, listing both folders), as for :meth:`find`.
        """
        if _is_path(name):
            return [self.find(name)]
        wanted = workspace_key(name)
        every = self.all()
        named = [record for record in every if record.name == wanted]
        if not named:
            known = sorted({record.name for record in every})
            raise WorkspaceNotFoundError(not_found("workspace", wanted, known=known))
        _refuse_ambiguous(wanted, [record for record in named if not record.archived])
        return named

    def start(self, root: Path, name: str, *, at: datetime) -> WorkspaceRecord:
        """Make ``root`` a project (``untaped project init``) and a new workspace called ``name``.

        Its meta row says workspace made the project, so ``remove`` may remove it.
        """
        project = init_project(root).project
        with project.locked():
            record = record_of(project)
            if record.meta is not None:
                from untaped_workspace.commands.create import Add  # noqa: PLC0415 - import cycle

                raise WorkspaceError(
                    f"workspace {q(record.name)} already exists at {shown_path(record.root)}",
                    category="conflict",
                    hint=Add.spelled(str(record.root), "--repo", "REPO"),
                )
            meta = WorkspaceMeta(
                name=name, project=project.id, created_by="workspace", created_at=at
            )
            META.of(project).add(meta)
            return record_of(project)

    def adopt(self, record: WorkspaceRecord, *, at: datetime) -> WorkspaceRecord:
        """Make the project a workspace if it isn't one yet (an ``add`` in any project)."""
        if record.meta is not None:
            return record
        name = validate_workspace_name(record.project.name)  # the folder's name names it
        meta = WorkspaceMeta(name=name, project=record.project.id, created_by="user", created_at=at)
        META.of(record.project).add(meta)
        return record.model_copy(update={"meta": meta})

    def hold(self, record: WorkspaceRecord, url: str) -> None:
        """Hold ``url``'s repo for the workspace's project (before its worktree is added)."""
        record.project.hold(HELD_REPOS.items(), store_ident(url))

    def put_repos(self, record: WorkspaceRecord, repos: Sequence[RepoSpec]) -> WorkspaceRecord:
        """Write ``repos``' rows (by folder: a row for the same folder is replaced)."""
        rows = WORKTREES.of(record.project)
        for spec in repos:
            rows.put(spec)
        return record_of(record.project)

    def set_archived(self, record: WorkspaceRecord, at: datetime | None) -> WorkspaceRecord:
        """Mark the workspace archived at ``at``, or active again with ``None``."""
        self._meta(record, lambda meta: meta.model_copy(update={"archived_at": at}))
        return record_of(record.project)

    def forget(self, record: WorkspaceRecord) -> None:
        """Drop workspace's rows from the project: its repos, then its meta row."""
        WORKTREES.of(record.project).replace_all(lambda _rows: [])
        if record.meta is not None:
            META.of(record.project).delete(record.meta.name)

    def release(
        self,
        record: WorkspaceRecord,
        *,
        force: bool,
        dry_run: bool,
        leaving: Sequence[WorkspaceRecord] = (),
    ) -> list[OutcomeRecord]:
        """Let go of every repo the project holds through workspace, through the holds service.

        ``leaving``: workspaces the same removal lets go of first, no other holder to this one.
        """
        store = HELD_REPOS.items()
        forced: tuple[Any, ...] = record.project.holds(store) if force else ()
        return record.project.release(
            store,
            force=forced,
            dry_run=dry_run,
            leaving=[other.project for other in leaving],
        )

    def remove_project(self, record: WorkspaceRecord) -> ProjectRemoval:
        """Remove the project as ``untaped project remove`` does, unless it still holds items."""
        return remove_project(record.project)

    def _meta(
        self, record: WorkspaceRecord, change: Callable[[WorkspaceMeta], WorkspaceMeta]
    ) -> None:
        if record.meta is None:
            raise WorkspaceNotFoundError(f"{shown_path(record.root)} is no workspace")
        META.of(record.project).update(record.meta.name, change)


def _usable(record: WorkspaceRecord) -> WorkspaceRecord:
    """``record``, unless it is a copy workspace runs no git in (:func:`copy_refusal`)."""
    refusal = copy_refusal(record)
    if refusal is not None:
        raise refusal
    return record


def _refuse_ambiguous(name: str, active: Sequence[WorkspaceRecord]) -> None:
    """Exit 2 when two active workspaces share ``name``, listing their folders."""
    if len(active) > 1:
        folders = ", ".join(shown_path(record.root) for record in active)
        raise UsageError(
            f"workspace {q(name)} is ambiguous: {folders}",
            hint="name it by its folder instead",
        )


def _is_path(name: str) -> bool:
    """Whether NAME is a folder rather than a workspace's name (it has a separator)."""
    return "/" in name or name.startswith(("~", ".")) or "\\" in name


def _archived_at(record: WorkspaceRecord) -> datetime:
    assert record.meta is not None and record.meta.archived_at is not None
    return record.meta.archived_at
