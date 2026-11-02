"""``untaped workspace list|status|path``: find workspaces and read their repos' git state."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import nullcontext

from pydantic import Field

from untaped.sdk import Matched, Output, Positional, UsageError
from untaped.sdk.storage import NO_PROGRESS
from untaped_workspace.application.archive import resume_hint
from untaped_workspace.commands._workspace import NAME_HELP, WorkspaceCommand
from untaped_workspace.records import RepoStatus, Workspace


class List(WorkspaceCommand):
    """List active workspaces (or archived ones): every project with workspace's rows.

    Only projects whose folder is here now are read: a workspace's rows
    live in its folder (`untaped project prune` handles a folder that is gone).
    """

    archived: bool = Field(False, description="List archived workspaces instead.")
    output: Output[Workspace]

    def run(self) -> Iterator[Workspace]:
        self.output.empty("No workspaces found.")
        for record in self.store().all():
            if record.meta is None or record.archived != self.archived:
                continue
            yield Workspace(
                name=record.name,
                repos=len(record.repos),
                created_at=record.meta.created_at,
                archived_at=record.meta.archived_at,
                target_path=record.root,
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
        if self.all_:
            records = [record for record in self.store().all() if not record.archived]
        else:
            records = [self.locate(self.name)]
            if records[0].archived:
                self.ctx.diag.info(
                    f"workspace {records[0].name} is archived; {resume_hint(records[0])}"
                )
                records = []
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
        return str(self.locate(self.name).root)
