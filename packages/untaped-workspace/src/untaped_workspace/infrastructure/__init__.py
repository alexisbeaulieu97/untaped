"""Workspace adapters: git worktrees on the repo store, state, and running commands.

The repo catalog (``infrastructure.catalog``) and the picker's source
(``infrastructure.pick_source``) load the contracts machinery, so they are
imported where they are used, never here.
"""

from untaped_workspace.infrastructure.command_runner import SubprocessRunner
from untaped_workspace.infrastructure.git_worktrees import LocalGitWorktrees
from untaped_workspace.infrastructure.state_store import StateWorkspaceStore

__all__ = ["LocalGitWorktrees", "StateWorkspaceStore", "SubprocessRunner"]
