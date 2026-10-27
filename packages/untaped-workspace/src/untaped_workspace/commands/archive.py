"""``untaped workspace archive|remove``: take a workspace's worktrees away, then its records."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from pydantic import Field

from untaped.sdk import Confirm, Output, Positional, now, plural, q
from untaped_workspace.application.archive import ArchiveWorkspace
from untaped_workspace.application.remove import RemovalPlan, RemoveWorkspace, refusal_hint
from untaped_workspace.commands._workspace import NAME_HELP, WorkspaceCommand
from untaped_workspace.domain.safety import archive_hint
from untaped_workspace.errors import WorkspaceError
from untaped_workspace.infrastructure import StateWorkspaceStore
from untaped_workspace.records import ArchiveOutcome, RemoveOutcome, RepoStatus


class Archive(WorkspaceCommand):
    """Remove the workspace's worktrees and directory, and record it as archived.

    Refuses (exit 1, nothing removed) while any repo has uncommitted,
    stashed or unpushed work, unless --force. Local branches stay in the
    repo store, so a later `create` with the same branch resumes the work;
    `remove` gives the space back. --dry-run previews with `planned` and
    `skipped` rows and exits 0, even when repos would block. --force on a
    blocked workspace confirms first, and without a terminal needs --yes
    (else exit 2). What blocks, and what --force discards: `untaped workspace --help`.

    Examples
    --------
    ```console
    $ untaped workspace status PROJ-123 --check
    $ untaped workspace archive PROJ-123
    ```
    """

    name: Positional[str | None] = Field(None, description=NAME_HELP)
    force: bool = Field(
        False,
        description="Archive even when repos have unpushed or uncommitted work (confirms first).",
    )
    confirm: Confirm
    output: Output[ArchiveOutcome]

    def run(self) -> Iterator[ArchiveOutcome]:
        located = self.locate(self.name)
        git = self.git_worktrees()
        archive = ArchiveWorkspace(
            StateWorkspaceStore(), git, workspaces_dir=self.workspaces_dir, now=now
        )
        with archive.hold(located.name) as record:  # from the check through the removal
            rows = self.status_reader(git)(record)
            blocked = [row for row in rows if row.blockers]
            if self.confirm.dry_run or (blocked and not self.force):
                yield from _plan(rows, force=self.force)
                if self.confirm.dry_run:
                    return
                raise WorkspaceError(
                    f"{plural(len(blocked), 'repo')} would lose work; nothing archived",
                    hint=archive_hint(blocked),
                )
            details = "; ".join(f"{row.dir} ({', '.join(row.blockers)})" for row in blocked)
            self.confirm(f"Archive {record.name} and discard: {details}?", needed=bool(blocked))
            outcomes = archive(record, force=self.force)
        self.report(outcomes, lambda row: f"{row.workspace}/{row.repo}")
        if not any(row.failed for row in outcomes):
            self.ctx.diag.success(f"archived workspace {q(record.name)}")
        yield from outcomes


class Remove(WorkspaceCommand):
    """Drop a workspace's records and release its repos from the repo store.

    An active workspace is archived first, with archive's checks. Each repo
    no other workspace (active or archived) uses is released: workspace's
    refs and the local branches whose commits are all pushed go, and the
    repo itself when nothing else uses it. Refuses (exit 1, nothing changed)
    while work would be lost, unless --force; a stash is never deleted.

    Always confirms; without a terminal it needs --yes (else exit 2).
    --dry-run previews with `planned` and `skipped` rows and exits 0. What
    stays and what --force deletes: `untaped workspace --help`.

    Examples
    --------
    ```console
    $ untaped workspace remove PROJ-123 --dry-run
    ```
    """

    name: Positional[str] = Field(description="Workspace to remove, active or archived.")
    force: bool = Field(
        False,
        description=(
            "Remove even when repos have uncommitted or unpushed work, deleting "
            "unpushed branches (confirms first)."
        ),
    )
    confirm: Confirm
    output: Output[RemoveOutcome]

    def run(self) -> Iterator[RemoveOutcome]:
        git = self.git_worktrees()
        remove = RemoveWorkspace(
            StateWorkspaceStore(),
            git,
            status=self.status_reader(git),
            workspaces_dir=self.workspaces_dir,
            now=now,
        )
        with remove.hold(self.name) as plan:  # from the check through the removal
            if self.confirm.dry_run or (plan.blocked and not self.force):
                yield from remove.preview(plan, force=self.force)
                if self.confirm.dry_run:
                    return
                raise WorkspaceError(
                    f"{plural(len(plan.blocked), 'repo')} would lose work; nothing removed",
                    hint=refusal_hint(plan),
                )
            self.confirm(_question(plan, force=self.force))
            outcomes = remove(plan, force=self.force)
        self.report(outcomes, lambda row: f"{row.workspace}/{row.repo}")
        if not any(row.failed for row in outcomes):
            self.ctx.diag.success(f"removed workspace {q(plan.name)}")
        yield from outcomes


def _plan(rows: Sequence[RepoStatus], *, force: bool) -> Iterator[ArchiveOutcome]:
    """What ``archive`` would do: ``skipped`` for blocked repos unless ``force``."""
    for row in rows:
        yield ArchiveOutcome(
            workspace=row.workspace,
            repo=row.repo,
            action="skipped" if row.blockers and not force else "planned",
            detail="; ".join(row.blockers),
            target_path=row.target_path,
        )


def _question(plan: RemovalPlan, *, force: bool) -> str:
    """The removal's question, naming the work ``--force`` would discard."""
    lost = [
        (repo.spec.dir, [*repo.blockers, *(repo.discards if force else ())]) for repo in plan.repos
    ]
    discard = "; ".join(f"{d} ({', '.join(work)})" for d, work in lost if work)
    return f"Remove workspace {plan.name} and release its repos" + (
        f", discarding: {discard}?" if discard else "?"
    )
