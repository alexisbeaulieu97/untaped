"""Workspace adapters: bare cache, git worktrees, state, and repo resolution."""

from untaped.capabilities.workspace.infrastructure.catalog import GithubRepoCatalog
from untaped.capabilities.workspace.infrastructure.command_runner import SubprocessRunner
from untaped.capabilities.workspace.infrastructure.git_worktrees import LocalGitWorktrees
from untaped.capabilities.workspace.infrastructure.state_store import StateWorkspaceStore

__all__ = ["GithubRepoCatalog", "LocalGitWorktrees", "StateWorkspaceStore", "SubprocessRunner"]
