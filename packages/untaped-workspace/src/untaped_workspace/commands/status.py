"""``untaped workspace list|status|path``: find workspaces and read their repos' git state."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import nullcontext

from pydantic import Field

from untaped.sdk import NO_PROGRESS, Matched, Output, Positional, UsageError
from untaped_workspace.commands._workspace import NAME_HELP, WorkspaceCommand
from untaped_workspace.domain.models import ArchivedRecord, WorkspaceRecord
from untaped_workspace.infrastructure import StateWorkspaceStore
from untaped_workspace.records import RepoStatus, Workspace


class List(WorkspaceCommand):
    """List active workspaces (or archived ones)."""

    archived: bool = Field(False, description="List archived workspaces instead.")
    output: Output[Workspace]

    def run(self) -> Iterator[Workspace]:
        store = StateWorkspaceStore()
        records: Sequence[WorkspaceRecord] = store.archived() if self.archived else store.active()
        self.output.empty("No workspaces found.")
        root = self.workspaces_dir
        for record in records:
            yield Workspace(
                name=record.name,
                repos=len(record.repos),
                created_at=record.created_at,
                archived_at=record.archived_at if isinstance(record, ArchivedRecord) else None,
                target_path=root / record.name,
            )


class Status(WorkspaceCommand):
    """Show each repo's branch, ahead/behind, local changes and archive blockers.

    Offline unless --fetch. A repo whose fetch fails keeps its local state,
    with an error (exit 1). --fetch also resumes a history backfill that
    stopped. A repo whose git state can't be read is an `error` row (exit
    1). --check is the gate before `archive`: it exits 3 while any repo
    blocks it, where `archive --dry-run` exits 0 either way.
    """

    name: Positional[str | None] = Field(None, description=NAME_HELP)
    all_: bool = Field(False, description="Every active workspace.")
    fetch: bool = Field(False, description="Fetch each repo first (status is offline).")
    check: bool = Field(False, description="Exit 3 when any repo would block `archive`.")
    output: Output[RepoStatus]

    def run(self) -> Iterator[RepoStatus]:
        if self.all_ and self.name is not None:
            raise UsageError("--all cannot be combined with a workspace name")
        records = StateWorkspaceStore().active() if self.all_ else [self.locate(self.name)]
        status = self.status_reader(self.git_worktrees())
        task = self.ctx.diag.progress("Fetching repos") if self.fetch else nullcontext(NO_PROGRESS)
        with task as progress:
            rows = [
                row
                for record in records
                for row in status(record, fetch=self.fetch, progress=progress)
            ]
        self.output.empty("No repos found.")
        self.report(rows, lambda row: f"{row.workspace}/{row.dir}")
        yield from rows
        if self.check and any(row.blockers for row in rows):
            raise Matched


class Path(WorkspaceCommand):
    """Print the workspace's absolute path.

    With no NAME, the workspace containing the current directory; outside
    one, exit 2.

    Examples
    --------
    ```console
    $ cd "$(untaped workspace path PROJ-123)"
    ```
    """

    name: Positional[str | None] = Field(None, description=NAME_HELP)

    def run(self) -> str:
        return str(self.workspace_dir(self.locate(self.name).name))
