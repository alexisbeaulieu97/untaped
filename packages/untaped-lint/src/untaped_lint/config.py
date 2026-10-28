"""What a run is configured with beyond its flags: the profile and the repository's file.

The profile's ``lint.ignore`` is one user's list for every repository; the
repository's ``.untaped/lint.yml`` carries what the repository decides (its
rule modules, readers and ignores). The two ignore lists add up.
lint's commands and a plugin with a lint command of its own (``untaped awx
lint``) build it through :func:`lint_config`, so no other plugin reads
lint's settings itself; it reads them here, outside the layers, as a
composition root does.
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


def lint_config(start: Path, *, config: Path | None = None, tree: bool = True) -> LintConfig:
    """The profile's ``lint.ignore`` and the repo file found from ``start``.

    ``config`` reads that file instead of the one found walking up, and the
    tree is then ``start`` itself (the file says which rules, you say
    where); otherwise the tree is the found file's root, or the project
    root of ``start`` without one. ``tree=False`` lints records only: the
    repository's rules and ignores still apply, but no file of it is read.
    """
    from untaped.sdk import checkout_root, installed_plugins, settings_of  # noqa: PLC0415
    from untaped_lint.application import repo_file  # noqa: PLC0415
    from untaped_lint.settings import LintSettings  # noqa: PLC0415

    settings = settings_of(LintSettings)
    path = config if config is not None else repo_file.find(start)
    repo = None
    if path is not None:
        installed = [plugin.name for plugin in installed_plugins()]
        repo = repo_file.read(path, installed=installed)
    root: Path | None
    if not tree:
        root = None
    elif config is not None:
        root = start.resolve()
    else:
        root = repo.root if repo is not None else checkout_root(start) or start.resolve()
    return LintConfig(ignore=tuple(settings.ignore), repo=repo, tree_root=root)
