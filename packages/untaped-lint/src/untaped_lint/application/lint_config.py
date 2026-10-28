"""What a lint run starts from: the profile's ``lint.ignore`` and the repository's lint file.

lint's commands and a plugin with a lint command of its own (``untaped awx
lint``) build their :class:`~untaped_lint.config.LintConfig` through
:func:`lint_config`, so no other plugin reads lint's settings itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untaped_lint.config import LintConfig


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
    from untaped_lint.config import LintConfig  # noqa: PLC0415
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
