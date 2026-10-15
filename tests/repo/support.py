"""Shared values for the workspace-level tests: repo paths and first-party names."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import NoReturn

import pytest
from release import baseline_tag  # scripts/release.py

from untaped.git import run_git

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES = REPO_ROOT / "packages"

#: The public-surface snapshot, relative to the repo root (``git show`` takes this form).
SURFACE_FILE = "tests/repo/surface.txt"

#: A fenced code block, indented or not.
FENCE = re.compile(r"^[ \t]*(```|~~~).*?^[ \t]*\1", re.MULTILINE | re.DOTALL)

#: Every first-party plugin, in name order.
FIRST_PARTY = (
    "ansible",
    "awx",
    "dotfiles",
    "git",
    "github",
    "jira",
    "lint",
    "recipe",
    "workspace",
)


def markdown_files() -> list[Path]:
    """Every hand-written or generated Markdown page users and agents read."""
    files = sorted((REPO_ROOT / "docs").rglob("*.md"))
    skills = sorted(REPO_ROOT.glob("packages/*/src/**/skills/**/*.md"))
    readmes = sorted(PACKAGES.glob("*/README.md"))
    root = (REPO_ROOT / name for name in ("README.md", "AGENTS.md", "CONTRIBUTING.md"))
    examples = sorted(REPO_ROOT.glob("examples/*/README.md"))
    return [*files, *skills, *readmes, *examples, *root]


def git(*args: str) -> str:
    """``git -C <repo> *args``'s stdout."""
    return run_git(["-C", str(REPO_ROOT), *args], timeout=60, capture=True).text


def no_baseline() -> NoReturn:
    """No release tag to compare with: a CI checkout without tags fails; a local one skips."""
    if os.environ.get("CI"):
        pytest.fail("no release tag reachable from HEAD: CI must check out with tags")
    pytest.skip("no release tag reachable from HEAD (fetch tags to run this check)")


def reachable_tags() -> list[str]:
    """The ``v*`` tags reachable from HEAD."""
    return git("tag", "--list", "v*", "--merged", "HEAD").split()


def release_baseline(*, prereleases: bool = False) -> str:
    """The newest release tag reachable from HEAD (see :func:`no_baseline` without one).

    ``prereleases`` counts release candidates too (see :func:`baseline_tag`).
    """
    tag = baseline_tag(reachable_tags(), prereleases=prereleases)
    if tag is None:
        no_baseline()
    return tag
