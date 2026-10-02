"""Shared values for the workspace-level tests: repo paths and first-party names."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES = REPO_ROOT / "packages"

#: Every first-party capability, in name order.
FIRST_PARTY = ("ansible", "awx", "dotfiles", "github", "jira", "recipe", "workspace")
