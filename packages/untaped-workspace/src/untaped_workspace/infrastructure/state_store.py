"""``StateWorkspaceStore``: active and archived workspaces in ``state.yml``.

Also the per-workspace advisory lock (``<workspaces_dir>/.<name>.lock``)
that serialises ``create``, ``add``, ``archive`` and ``remove`` of one workspace.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from untaped.sdk import RowExists, file_lock, not_found, q
from untaped_workspace.domain.models import ArchivedRecord, RepoSpec, WorkspaceRecord
from untaped_workspace.domain.naming import repo_key, workspace_key
from untaped_workspace.errors import WorkspaceError, WorkspaceNotFoundError
from untaped_workspace.settings import ACTIVE, ARCHIVED

_BUSY_HINT = "wait for the other untaped command on this workspace to finish, then retry"


class StateWorkspaceStore:
    """Workspace records kept under ``workspace.active`` / ``workspace.archived``.

    ``workspaces_dir`` is only needed by :meth:`locked`.
    """

    def __init__(self, *, workspaces_dir: Path | None = None, lock_timeout: float = 600.0) -> None:
        self._workspaces_dir = workspaces_dir
        self._lock_timeout = lock_timeout

    @contextmanager
    def locked(self, name: str) -> Iterator[None]:
        """Hold workspace ``name``'s lock; another process waits, then fails as busy."""
        name = workspace_key(name)
        if self._workspaces_dir is None:
            raise WorkspaceError("this workspace store was built without a workspaces_dir")
        root = self._workspaces_dir.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        with file_lock(
            root / f".{name}.lock",
            timeout=self._lock_timeout,
            error=lambda message: WorkspaceError(message, hint=_BUSY_HINT),
            busy=f"workspace {name} is busy (another untaped process)",
            failed=f"could not lock workspace {name}",
        ):
            yield

    def active(self) -> list[WorkspaceRecord]:
        return ACTIVE.all()

    def names(self) -> list[str]:
        return ACTIVE.keys()

    def archived(self) -> list[ArchivedRecord]:
        # As StateRows reads rows: a newer untaped's fields never fail them.
        return [ArchivedRecord.model_validate(row, extra="ignore") for row in ARCHIVED.entries()]

    def get(self, name: str) -> WorkspaceRecord | None:
        return ACTIVE.get(workspace_key(name))

    def create(self, record: WorkspaceRecord) -> None:
        try:
            ACTIVE.add(record)
        except RowExists:
            raise WorkspaceError(
                f"workspace {q(record.name)} already exists",
                category="conflict",
                hint=f"run `untaped workspace add {record.name} --repo REPO`",
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
            raise WorkspaceNotFoundError(not_found("workspace", name, known=ACTIVE.keys()))
        archived = ArchivedRecord(**record.model_dump(), archived_at=at)
        ARCHIVED.mutate(lambda rows: [*rows, archived.model_dump(mode="json")])
        ACTIVE.delete(name)
        return archived

    def remove(self, name: str) -> None:
        """Drop every record named ``name``, active and archived (callers hold :meth:`locked`).

        The active one goes first: a failure in between leaves an archived
        record, which ``remove`` drops on a retry.
        """
        name = workspace_key(name)

        def _drop(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [row for row in rows if row.get("name") != name]

        ACTIVE.delete(name)
        ARCHIVED.mutate(_drop)

    def _update(
        self, name: str, change: Callable[[WorkspaceRecord], WorkspaceRecord]
    ) -> WorkspaceRecord:
        name = workspace_key(name)
        updated = ACTIVE.update(name, change)
        if updated is None:
            raise WorkspaceNotFoundError(not_found("workspace", name, known=ACTIVE.keys()))
        return updated
