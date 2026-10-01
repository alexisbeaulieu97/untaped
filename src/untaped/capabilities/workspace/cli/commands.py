"""Workspace command tree (commands return in a later task)."""

from __future__ import annotations

from untaped.capability_api import create_app

app = create_app(
    name="workspace",
    help="Create and archive task workspaces (git worktrees of several repos).",
)
