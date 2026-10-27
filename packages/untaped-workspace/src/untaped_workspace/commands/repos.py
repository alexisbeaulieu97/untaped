"""``untaped workspace repos``: inspect and refresh a workspace's repos."""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import Field

from untaped.sdk import Group, Output, Positional
from untaped_workspace.application.resolve import ResolveRepos
from untaped_workspace.commands._workspace import NAME_HELP, WorkspaceCommand
from untaped_workspace.infrastructure import StateWorkspaceStore
from untaped_workspace.records import ResolveOutcome


class Resolve(WorkspaceCommand):
    """Ask each repo's source again and save the URL it lists now.

    A repo's URL is frozen when it joins the workspace; this picks up a
    change on the provider's side (a new github.git_protocol, say). A new URL
    that names another store repo is refused: remove the repo and add it
    again. Repos added by URL have no source and are skipped.
    """

    name: Positional[str | None] = Field(None, description=NAME_HELP)
    output: Output[ResolveOutcome]

    def run(self) -> Iterator[ResolveOutcome]:
        from untaped_workspace.infrastructure.catalog import RepoSources  # noqa: PLC0415

        record = self.locate(self.name)
        resolve = ResolveRepos(
            StateWorkspaceStore(),
            self.git_worktrees(),
            RepoSources(),
            workspaces_dir=self.workspaces_dir,
        )
        rows = resolve(record.name)
        self.output.empty("No repos found.")
        self.report(rows, lambda row: f"{row.workspace}/{row.dir}")
        return iter(rows)


class ReposGroup(Group):
    """Inspect and refresh a workspace's repos."""

    commands = (Resolve,)
