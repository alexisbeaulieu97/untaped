"""``StateWorkspaceStore``: active and archived workspaces in ``state.yml``."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from untaped.capabilities.workspace.domain.models import ArchivedRecord, RepoSpec, WorkspaceRecord
from untaped.capabilities.workspace.errors import WorkspaceError, WorkspaceNotFoundError
from untaped.capability_api import StateCollection, hint, not_found, q


def _dump(record: WorkspaceRecord) -> dict[str, Any]:
    return record.model_dump(mode="json")


class StateWorkspaceStore:
    """Workspace records kept under ``workspace.active`` / ``workspace.archived``."""

    def __init__(self) -> None:
        self._active = StateCollection("workspace", "active", id_field="name")
        self._archived = StateCollection("workspace", "archived", id_field="name")

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
                    hint=hint(f"workspace add {record.name} --repo REPO"),
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
                    updated = current.model_copy(update={"repos": (*current.repos, *repos)})
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
        record = self.get(name)
        if record is None:
            raise WorkspaceNotFoundError(
                not_found("workspace", name, known=[r.name for r in self.active()])
            )
        archived = ArchivedRecord(**record.model_dump(), archived_at=at)
        self._archived.mutate(lambda rows: [*rows, _dump(archived)])
        self._active.remove(name)
        return archived
