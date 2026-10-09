"""Only the terminal adapter imports prompt_toolkit.

A screen's models, updates and views never touch the terminal library, so the
adapter can be replaced without changing a screen. This test applies core's
``terminal-boundary`` rule (which ``check_conventions`` applies to one
plugin) to every package's source, core's included, the example plugin and
``scripts``, and allows only the adapter. The rule's inline waiver
(``# untaped: allow terminal-boundary`` on the import line) applies here too.
"""

from __future__ import annotations

from pathlib import Path

from repo.support import PACKAGES, REPO_ROOT
from untaped.conventions.source import source_files
from untaped.conventions.terminal_boundary import terminal_boundary_violations

#: The adapter, as a violation names it (relative to its package's ``src``).
ADAPTER = "untaped/screen/terminal.py"


def source_dirs() -> list[Path]:
    """Every shipped package directory, the example plugin's and ``scripts``."""
    roots = [*PACKAGES.glob("*/src/*"), *(REPO_ROOT / "examples").glob("*/src/*")]
    return [*sorted(root for root in roots if root.is_dir()), REPO_ROOT / "scripts"]


def violations() -> list[str]:
    return [
        found
        for directory in source_dirs()
        for found in terminal_boundary_violations(directory, list(source_files(directory)))
    ]


def test_only_the_adapter_imports_prompt_toolkit() -> None:
    stray = [found for found in violations() if not found.startswith(f"{ADAPTER}:")]
    assert not stray, f"{stray}: only {ADAPTER} may import prompt_toolkit"


def test_the_adapter_still_imports_prompt_toolkit() -> None:
    assert any(found.startswith(f"{ADAPTER}:") for found in violations())
