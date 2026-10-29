"""What the workspace commands share: the base class and the workers.

Every command reads the plugin's settings as ``Command[WorkspaceSettings]``
and builds the adapters it needs from them through :class:`WorkspaceCommand`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import Command, Parallel, key
from untaped_workspace.application.locate import locate_workspace, workspace_root
from untaped_workspace.application.status import WorkspaceStatus
from untaped_workspace.infrastructure import LocalGitWorktrees, StateWorkspaceStore
from untaped_workspace.infrastructure.state_store import CHECKOUTS
from untaped_workspace.settings import MAX_WORKERS, WorkspaceSettings

if TYPE_CHECKING:
    from untaped.sdk import TargetRecord
    from untaped_workspace.domain.models import WorkspaceRecord

#: How many repos a command works on at once: ``-j``, else ``workspace.parallel``,
#: else ``min(8, 2 x CPUs)``; a value above ``2 x CPUs`` is clamped with a warning.
WORKERS = Parallel(default=key(WorkspaceSettings).parallel, unset=8, cap=MAX_WORKERS)

#: The help of the NAME most commands take.
NAME_HELP = "Workspace name. Default: the workspace containing the current directory."


class WorkspaceCommand(Command[WorkspaceSettings]):
    """Base of the workspace commands: the settings' folders and the adapters built from them."""

    @property
    def workspaces_dir(self) -> Path:
        """``workspace.workspaces_dir``, else ``plugins/workspace/checkouts/`` under the home."""
        return CHECKOUTS.path.resolve()

    def workspace_dir(self, name: str) -> Path:
        """Absolute directory of workspace ``name``."""
        return workspace_root(self.workspaces_dir, name)

    def locate(self, name: str | None) -> WorkspaceRecord:
        """The workspace ``name``, or the one containing the current directory."""
        return locate_workspace(
            StateWorkspaceStore(), name=name, workspaces_dir=self.workspaces_dir, cwd=Path.cwd()
        )

    def git_worktrees(self) -> LocalGitWorktrees:
        return LocalGitWorktrees(plugin=self.ctx.plugin)

    def status_reader(self, git: LocalGitWorktrees) -> WorkspaceStatus:
        """The ``status`` use case, also ``archive``'s safety check, ``workspace.parallel`` wide."""
        return WorkspaceStatus(git, workspaces_dir=self.workspaces_dir, parallel=WORKERS.value)

    def report[R: TargetRecord](self, rows: Iterable[R], item: Callable[[R], str]) -> None:
        """One stderr failure per row that carries an error, naming its ``item``."""
        for row in rows:
            if row.error is not None:
                self.ctx.diag.failure(row.error, item=item(row))
