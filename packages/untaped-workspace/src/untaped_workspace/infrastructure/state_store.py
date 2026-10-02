"""``StateWorkspaceStore``: active and archived workspaces in ``state.yml``.

Also the per-workspace advisory lock (``<workspaces_dir>/.<name>.lock``)
that serialises ``create``, ``add`` and ``archive`` of one workspace.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from untaped.sdk import StateCollection, cache_key, file_lock, not_found, q
from untaped_workspace.domain.models import ArchivedRecord, RepoSpec, WorkspaceRecord
from untaped_workspace.errors import WorkspaceError, WorkspaceNotFoundError

_BUSY_HINT = "wait for the other untaped command on this workspace to finish, then retry"


def _dump(record: WorkspaceRecord) -> dict[str, Any]:
    return record.model_dump(mode="json")


class StateWorkspaceStore:
    """Workspace records kept under ``workspace.active`` / ``workspace.archived``.

    ``workspaces_dir`` is only needed by :meth:`locked`.
    """

    def __init__(self, *, workspaces_dir: Path | None = None, lock_timeout: float = 600.0) -> None:
        self._active = StateCollection("workspace", "active", id_field="name")
        self._archived = StateCollection("workspace", "archived", id_field="name")
        self._workspaces_dir = workspaces_dir
        self._lock_timeout = lock_timeout

    @contextmanager
    def locked(self, name: str) -> Iterator[None]:
        """Hold workspace ``name``'s lock; another process waits, then fails as busy."""
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
        return [WorkspaceRecord.model_validate(row) for row in self._active.entries()]

    def archived(self) -> list[ArchivedRecord]:
        return [ArchivedRecord.model_validate(row) for row in self._archived.entries()]

    def get(self, name: str) -> WorkspaceRecord | None:
        row = self._active.get(name)
        return None if row is None else WorkspaceRecord.model_validate(row)

    def create(self, record: WorkspaceRecord) -> None:
        def _insert(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            if any(row.get("name") == record.name for row in rows):
                raise WorkspaceError(
                    f"workspace {q(record.name)} already exists",
                    category="conflict",
                    hint=f"run `untaped workspace add {record.name} --repo REPO`",
                )
            return [*rows, _dump(record)]

        self._active.mutate(_insert)

    def add_repos(self, name: str, repos: Sequence[RepoSpec]) -> WorkspaceRecord:
        updated: WorkspaceRecord | None = None

        def _append(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            nonlocal updated
            out: list[dict[str, Any]] = []
            for row in rows:
                if row.get("name") == name:
                    current = WorkspaceRecord.model_validate(row)
                    have = {cache_key(spec.url) for spec in current.repos}
                    new = [spec for spec in repos if cache_key(spec.url) not in have]
                    updated = current.model_copy(update={"repos": (*current.repos, *new)})
                    row = _dump(updated)
                out.append(row)
            if updated is None:
                raise WorkspaceNotFoundError(
                    not_found("workspace", name, known=[r.get("name") for r in rows])
                )
            return out

        self._active.mutate(_append)
        assert updated is not None
        return updated

    def archive(self, name: str, *, at: datetime) -> ArchivedRecord:
        """Move ``name`` to the archived list (callers hold :meth:`locked`).

        The archived copy is written first: a failure in between leaves a
        recoverable duplicate rather than losing the workspace's history.
        """
        record = self.get(name)
        if record is None:
            raise WorkspaceNotFoundError(
                not_found("workspace", name, known=[r.name for r in self.active()])
            )
        archived = ArchivedRecord(**record.model_dump(), archived_at=at)
        self._archived.mutate(lambda rows: [*rows, _dump(archived)])
        self._active.remove(name)
        return archived
