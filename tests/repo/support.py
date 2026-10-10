"""Shared values for the workspace-level tests: repo paths and first-party names."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES = REPO_ROOT / "packages"

#: A fenced code block, indented or not.
FENCE = re.compile(r"^[ \t]*(```|~~~).*?^[ \t]*\1", re.MULTILINE | re.DOTALL)

#: Every first-party plugin, in name order.
FIRST_PARTY = ("ansible", "awx", "dotfiles", "github", "jira", "recipe", "workspace")


def markdown_files() -> list[Path]:
    """Every hand-written or generated Markdown page users and agents read."""
    files = sorted((REPO_ROOT / "docs").rglob("*.md"))
    skills = sorted(REPO_ROOT.glob("packages/*/src/**/skills/**/*.md"))
    readmes = sorted(PACKAGES.glob("*/README.md"))
    root = (REPO_ROOT / name for name in ("README.md", "AGENTS.md", "CONTRIBUTING.md"))
    examples = sorted(REPO_ROOT.glob("examples/*/README.md"))
    return [*files, *skills, *readmes, *examples, *root]
