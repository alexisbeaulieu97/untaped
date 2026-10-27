"""``StateWorkspaceStore``: active and archived workspaces in the plugin's state.

The plugin class holds the blocks declared here: the active rows, the
archived ones, and ``checkouts``, the workspaces folder, whose lock of one
workspace serialises ``create``, ``add``, ``archive`` and ``remove`` of it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta

from untaped.sdk import Dir, RowExists, StateRows, key, not_found, q
from untaped_workspace.domain.models import ArchivedRecord, RepoSpec, WorkspaceRecord
from untaped_workspace.domain.naming import repo_key, workspace_key
from untaped_workspace.errors import WorkspaceError, WorkspaceNotFoundError
from untaped_workspace.settings import WorkspaceSettings

#: Active workspaces, one row per name. No command marks ``removes``: ``workspace
#: remove`` plans from every record, so a damaged one is fixed by hand.
WORKSPACES = StateRows[WorkspaceRecord]()
#: Archived workspaces: a list, since ``archive`` may keep a name twice.
ARCHIVED = StateRows[ArchivedRecord]()
#: Every workspace's folder, ``<workspaces_dir>/NAME``, and the lock of each.
CHECKOUTS = Dir(at=key(WorkspaceSettings).workspaces_dir, wait=timedelta(minutes=10))


class StateWorkspaceStore:
    """Workspace records kept in the plugin's ``workspaces`` and ``archived`` rows."""

    @contextmanager
    def locked(self, name: str) -> Iterator[None]:
        """Hold workspace ``name``'s lock; another process waits, then fails as busy."""
        with CHECKOUTS.locked(workspace_key(name)):
            yield

    def active(self) -> list[WorkspaceRecord]:
        return WORKSPACES.all()

    def names(self) -> list[str]:
        return WORKSPACES.keys()

    def archived(self) -> list[ArchivedRecord]:
        return ARCHIVED.all()

    def get(self, name: str) -> WorkspaceRecord | None:
        return WORKSPACES.get(workspace_key(name))

    def create(self, record: WorkspaceRecord) -> None:
        try:
            WORKSPACES.add(record)
        except RowExists:
            from untaped_workspace.commands.create import Add  # noqa: PLC0415 - import cycle

            raise WorkspaceError(
                f"workspace {q(record.name)} already exists",
                category="conflict",
                hint=Add.spelled(record.name, "--repo", "REPO"),
            ) from None

    def add_repos(self, name: str, repos: Sequence[RepoSpec]) -> WorkspaceRecord:
        def _append(current: WorkspaceRecord) -> WorkspaceRecord:
            have = {repo_key(spec.url) for spec in current.repos}
            new = tuple(spec for spec in repos if repo_key(spec.url) not in have)
            return current.model_copy(update={"repos": (*current.repos, *new)})

        return self._update(name, _append)

    def update_repos(self, name: str, repos: Sequence[RepoSpec]) -> WorkspaceRecord:
        """Replace the repos of active workspace ``name`` that share a ``dir`` with ``repos``."""
        by_dir = {spec.dir: spec for spec in repos}

        def _replace(current: WorkspaceRecord) -> WorkspaceRecord:
            specs = tuple(by_dir.get(spec.dir, spec) for spec in current.repos)
            return current.model_copy(update={"repos": specs})

        return self._update(name, _replace)

    def archive(self, name: str, *, at: datetime) -> ArchivedRecord:
        """Move ``name`` to the archived list (callers hold :meth:`locked`).

        The archived copy is written first: a failure in between leaves a
        recoverable duplicate rather than losing the workspace's history.
        """
        name = workspace_key(name)
        record = self.get(name)
        if record is None:
            raise WorkspaceNotFoundError(not_found("workspace", name, known=WORKSPACES.keys()))
        archived = ArchivedRecord(**record.model_dump(), archived_at=at)
        ARCHIVED.append(archived)
        WORKSPACES.delete(name)
        return archived

    def remove(self, name: str) -> None:
        """Drop every record named ``name``, active and archived (callers hold :meth:`locked`).

        The active one goes first: a failure in between leaves an archived
        record, which ``remove`` drops on a retry.
        """
        name = workspace_key(name)
        WORKSPACES.delete(name)
        ARCHIVED.replace_all(lambda rows: [row for row in rows if row.name != name])

    def _update(
        self, name: str, change: Callable[[WorkspaceRecord], WorkspaceRecord]
    ) -> WorkspaceRecord:
        name = workspace_key(name)
        updated = WORKSPACES.update(name, change)
        if updated is None:
            raise WorkspaceNotFoundError(not_found("workspace", name, known=WORKSPACES.keys()))
        return updated
