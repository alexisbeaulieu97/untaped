"""What a run is configured with beyond its flags: the profile and the repository's file.

The profile's ``lint.ignore`` is one user's list for every repository; the
repository's ``.untaped/lint.yml`` carries what the repository decides (its
rule modules, readers and ignores). The two ignore lists add up.
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

    @classmethod
    def for_root(cls, start: Path, *, config: Path | None = None) -> LintConfig:
        """The profile's ``lint.ignore`` and the repo file found from ``start``.

        ``config`` reads that file instead of the one found walking up, and
        the tree is then ``start`` itself (the file says which rules, you say
        where); otherwise the tree is the found file's root, or the project
        root of ``start`` without one.
        """
        from untaped.sdk import get_config_section, installed_plugins, project_root  # noqa: PLC0415
        from untaped_lint.application import repo_file  # noqa: PLC0415
        from untaped_lint.settings import LintSettings  # noqa: PLC0415

        settings = get_config_section("lint", LintSettings)
        path = config if config is not None else repo_file.find(start)
        repo = None
        if path is not None:
            installed = [plugin.name for plugin in installed_plugins()]
            repo = repo_file.read(path, installed=installed)
        if config is not None:
            root = start.resolve()
        else:
            root = repo.root if repo is not None else project_root(start)
        return cls(ignore=tuple(settings.ignore), repo=repo, tree_root=root)
