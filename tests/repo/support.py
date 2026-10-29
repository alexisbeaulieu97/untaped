"""Shared values for the workspace-level tests: repo paths and first-party names."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import pytest
from release import baseline_tag  # scripts/release.py

from untaped.sdk.git import run_git

if TYPE_CHECKING:
    from untaped.sdk._run import Composition

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

#: The example plugins, by name and entry point target.
EXAMPLES = (
    ("hello", "untaped_hello.plugin:Hello"),
    ("shelf", "untaped_shelf.plugin:Shelf"),
    ("library", "untaped_library.plugin:Library"),
)


@contextmanager
def everything_composed() -> Iterator[Composition]:
    """A run composing every installed plugin and the examples: every kind there is."""
    from untaped.runtime.registry import PluginCandidate, discover_candidates
    from untaped.sdk._run import current_run
    from untaped.sdk.testing import compose_with

    sources = [str(path) for path in sorted(REPO_ROOT.glob("examples/*/src"))]
    examples = [
        PluginCandidate(distribution=f"untaped-{name}", name=name, target=target)
        for name, target in EXAMPLES
    ]
    sys.path[:0] = sources
    try:
        with compose_with(*discover_candidates(), *examples) as result:
            assert not result.quarantine, result.quarantine
            composed = {plugin.name for plugin in result.plugins}
            assert {*FIRST_PARTY, *(name for name, _ in EXAMPLES)} <= composed, composed
            yield current_run().composition
    finally:
        for source in sources:
            sys.path.remove(source)


def markdown_files() -> list[Path]:
    """Every hand-written or generated Markdown page users and agents read."""
    files = sorted((REPO_ROOT / "docs").rglob("*.md"))
    readmes = sorted(PACKAGES.glob("*/README.md"))
    root = (REPO_ROOT / name for name in ("README.md", "AGENTS.md", "CONTRIBUTING.md"))
    examples = sorted(REPO_ROOT.glob("examples/*/README.md"))
    return [*files, *readmes, *examples, *root]


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
