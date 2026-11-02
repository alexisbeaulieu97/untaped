"""Only core's process block makes process groups, and only core handles signals.

``untaped.sdk.process`` starts every child in its own group, stops the whole
group on a timeout or interrupt, and receives the SIGINT, SIGTERM and SIGHUP
untaped's entry point passes on; a group made by hand escapes all of that,
and a second signal handler replaces the one that stops every child. This test
applies lint's ``process-group`` and ``signal-handler`` rules (which ``untaped
lint`` applies to one plugin) to every package's source, core's included, the
example plugins and ``scripts``, and allows only the block's private module and
the terminal adapter, whose prompt_toolkit replaces the handlers while a screen
runs (it hands each interrupt on through ``ending``; the screen tests cover it).
The rules' inline waiver applies here too.
"""

from __future__ import annotations

from pathlib import Path

from repo.support import PACKAGES, REPO_ROOT
from untaped_lint.infrastructure.files import source_files
from untaped_lint.providers.conventions.process_boundary import process_boundary_violations

#: The block's private module, as a violation names it (relative to its package's ``src``).
BLOCK = "untaped/sdk/_process_groups.py"
#: The terminal adapter: it puts the handlers a screen needs in place and back.
ADAPTER = "untaped/screen/terminal.py"


def source_dirs() -> list[Path]:
    """Every shipped package directory, the example plugins' and ``scripts``."""
    roots = [*PACKAGES.glob("*/src/*"), *(REPO_ROOT / "examples").glob("*/src/*")]
    return [*sorted(root for root in roots if root.is_dir()), REPO_ROOT / "scripts"]


def violations() -> list[str]:
    return [
        found
        for directory in source_dirs()
        for found in process_boundary_violations(directory, list(source_files(directory)))
    ]


def _allowed(found: str) -> bool:
    if found.startswith(f"{BLOCK}:"):
        return True
    return found.startswith(f"{ADAPTER}:") and "::signal-handler::" in found


def test_only_the_block_makes_process_groups_and_handles_signals() -> None:
    stray = [found for found in violations() if not _allowed(found)]
    assert not stray, f"{stray}: only {BLOCK} (and {ADAPTER}, for signals) may"


def test_the_block_still_makes_groups_and_handles_signals() -> None:
    found = [each for each in violations() if each.startswith(f"{BLOCK}:")]
    assert any("::process-group::" in each for each in found)
    assert any("::signal-handler::" in each for each in found)
