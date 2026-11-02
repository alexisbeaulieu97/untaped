"""``untaped workspace archive|remove``: take a workspace's worktrees away, then its repos."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from pydantic import Field

from untaped.sdk import Confirm, Output, Positional, now, plural, q, shown_path
from untaped_workspace.application.archive import ArchiveWorkspace
from untaped_workspace.application.remove import RemovalPlan, RemoveWorkspace
from untaped_workspace.commands._workspace import NAME_HELP, WorkspaceCommand
from untaped_workspace.domain.safety import archive_hint
from untaped_workspace.errors import WorkspaceError
from untaped_workspace.records import ArchiveOutcome, RemoveOutcome, RepoStatus


class Archive(WorkspaceCommand):
    """Remove the workspace's worktrees; keep its folder, and the repos it holds.

    Refuses (exit 1, nothing removed) while any repo has uncommitted,
    stashed or unpushed work, unless --force. The project keeps its repos
    and their branches, so `create NAME` (or `add` in another project)
    resumes the work; `remove` gives the space back. --dry-run previews with `planned` and
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
        archive = ArchiveWorkspace(self.store(), git, now=now)
        with archive.hold(located) as record:  # from the check through the removal
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
    """Let go of a workspace's repos, drop its rows, and the project it made.

    Every workspace of NAME, active and archived. Each repo's worktree goes;
    the repo is released from the repo store, or kept when another project
    holds it. Refuses (exit 1, nothing changed) while a worktree has
    uncommitted, stashed or unpushed work, or the workspace's branch has
    commits no remote has, unless --force; a stash is never deleted. Then
    the workspace's rows go, and the project itself when `create` made it
    and it holds nothing else: config.yml and your own files stay, as with
    `untaped project remove`.

    Always confirms; without a terminal it needs --yes (else exit 2). A
    workspace whose repos change while the question waits is kept (exit 1).
    --dry-run previews with `planned` and `refused` rows and exits 0.

    Examples
    --------
    ```console
    $ untaped workspace remove PROJ-123 --dry-run
    ```
    """

    name: Positional[str] = Field(
        description="Workspace to remove, active or archived: its name, or its folder."
    )
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
        store = self.store()
        remove = RemoveWorkspace(store)
        located = store.named(self.name)
        with remove.hold(self.name, located, force=self.force) as plan:
            if self.confirm.dry_run or plan.blocked:
                yield from plan.rows
                if self.confirm.dry_run:
                    return
                raise WorkspaceError(
                    f"{plural(len(plan.blocked), 'repo')} would lose work; nothing removed",
                    hint="; ".join([*dict.fromkeys(plan.hints), "or pass --force to remove anyway"])
                    if plan.hints
                    else "pass --force to remove anyway",
                )
            self.confirm(_question(plan), preview=lambda: _preview(plan))
            outcomes = remove(plan, force=self.force)
        self.report(outcomes, lambda row: f"{row.workspace}/{row.repo}")
        if not any(row.failed or row.action in {"kept", "refused"} for row in outcomes):
            self.ctx.diag.success(f"removed workspace {q(located[0].name)}")
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


def _question(plan: RemovalPlan) -> str:
    """The removal's question, naming how many projects it removes."""
    count = len(plan.records)
    which = f"{count} workspaces named {plan.name}" if count > 1 else f"workspace {plan.name}"
    return f"Remove {which} and release its repos?"


def _preview(plan: RemovalPlan) -> list[str]:
    """What the question shows first: each repo's planned row."""
    return [
        f"{shown_path(row.target_path)} {row.repo or '(workspace)'}: {row.detail}"
        if row.target_path is not None
        else f"{row.workspace} {row.repo or '(workspace)'}: {row.detail}"
        for row in plan.rows
    ]
