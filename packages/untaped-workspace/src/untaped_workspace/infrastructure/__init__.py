"""Workspace adapters: worktrees on the repo store, workspaces in projects, running commands.

The repo catalog (``infrastructure.catalog``) and the picker's source
(``infrastructure.pick_source``) load the contracts machinery, so they are
imported where they are used, never here.
"""

from untaped_workspace.infrastructure.command_runner import SubprocessRunner
from untaped_workspace.infrastructure.git_worktrees import LocalGitWorktrees
from untaped_workspace.infrastructure.project_store import ProjectWorkspaceStore

__all__ = ["LocalGitWorktrees", "ProjectWorkspaceStore", "SubprocessRunner"]
