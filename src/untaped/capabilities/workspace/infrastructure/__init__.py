"""Workspace adapters: bare cache, git worktrees, state, and repo resolution."""

from untaped.capabilities.workspace.infrastructure.git_worktrees import LocalGitWorktrees
from untaped.capabilities.workspace.infrastructure.state_store import StateWorkspaceStore

__all__ = ["LocalGitWorktrees", "StateWorkspaceStore"]
