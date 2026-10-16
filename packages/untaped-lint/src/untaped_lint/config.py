"""What a run is configured with beyond its flags: the profile and the repository's file.

The profile's ``lint.ignore`` is one user's list for every repository; the
repository's ``.untaped/lint.yml`` carries what the repository decides (its
rule modules, readers and ignores). The two ignore lists add up. The
command builds it; this module only holds it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untaped_lint.application.repo_file import RepoFile


@dataclass(frozen=True)
class LintConfig:
    """The suppressions a run starts from, each ``<rule>[:<target-glob>]``, and its repo file."""

    ignore: tuple[str, ...] = ()
    """The profile's ``lint.ignore``."""
    repo: RepoFile | None = None
    tree_root: Path | None = None
    """Where the run's ``Tree`` comes from; ``None`` when it lints records only."""
