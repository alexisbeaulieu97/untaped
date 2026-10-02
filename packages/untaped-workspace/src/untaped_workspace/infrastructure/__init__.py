"""Workspace adapters: bare cache, git worktrees, state, and repo resolution."""

from untaped_workspace.infrastructure.catalog import GithubRepoCatalog
from untaped_workspace.infrastructure.command_runner import SubprocessRunner
from untaped_workspace.infrastructure.git_worktrees import LocalGitWorktrees
from untaped_workspace.infrastructure.state_store import StateWorkspaceStore

__all__ = ["GithubRepoCatalog", "LocalGitWorktrees", "StateWorkspaceStore", "SubprocessRunner"]
