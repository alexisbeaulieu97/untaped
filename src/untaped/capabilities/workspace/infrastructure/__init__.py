"""Workspace adapters: bare cache, git worktrees, state, and repo resolution."""

from untaped.capabilities.workspace.infrastructure.git_worktrees import LocalGitWorktrees

__all__ = ["LocalGitWorktrees"]
