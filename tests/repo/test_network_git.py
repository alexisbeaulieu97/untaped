"""Network git goes through the git plugin: no other plugin clones, fetches, pulls or pushes.

The git plugin's repo store and remote queries carry what a host plugin
supplies (``GitHost`` credentials), untaped's proxy and the store's object
policy; a plugin that runs ``git clone`` itself gets none of that, so a
private repo on a host only a plugin answers for fails there alone. This
test reads every plugin's source but git's and flags a list or tuple that
starts a network git command: ``clone``, ``fetch``, ``pull``, ``ls-remote``
or ``push`` first, as passed to ``run_git``, or after ``"git"``, as passed to
``subprocess``. Its exceptions only shrink.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from repo.support import PACKAGES

MESSAGE = (
    "network git goes through the git plugin (GitHost credentials, proxy, store): untaped_git.api"
)
VERBS = frozenset({"clone", "fetch", "pull", "ls-remote", "push"})
#: Modules allowed to run network git themselves, relative to ``packages/``, each with why.
EXCEPTIONS = {
    # Dotfiles' managed clones, until the dotfiles redesign moves them onto git.
    "untaped-dotfiles/src/untaped_dotfiles/git_repos.py",
    # The remote of the user's own checkout, asked with that checkout's own git config.
    "untaped-awx/src/untaped_awx/infrastructure/git_head.py",
    "untaped-awx/src/untaped_awx/infrastructure/git_source.py",
}


def network_git(source: str) -> list[int]:
    """The lines of ``source`` holding a list or tuple that starts a network git command."""
    return sorted(
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.List, ast.Tuple))
        and (_word(node, 0) in VERBS or (_word(node, 0) == "git" and _word(node, 1) in VERBS))
    )


def _word(node: ast.List | ast.Tuple, index: int) -> str | None:
    """The string literal at ``index`` of ``node``, if that is what it holds."""
    if len(node.elts) <= index:
        return None
    element = node.elts[index]
    if isinstance(element, ast.Constant) and isinstance(element.value, str):
        return element.value
    return None


def plugin_sources() -> list[Path]:
    """Every source file of every package (core included) but git's."""
    return sorted(
        path
        for package in PACKAGES.iterdir()
        if package.name != "untaped-git"
        for path in (package / "src").rglob("*.py")
    )


def test_only_the_git_plugin_runs_network_git() -> None:
    found = [
        f"{relative}:{line}"
        for path in plugin_sources()
        if (relative := path.relative_to(PACKAGES).as_posix()) not in EXCEPTIONS
        for line in network_git(path.read_text(encoding="utf-8"))
    ]
    assert not found, f"{found}: {MESSAGE}"


def test_every_exception_still_runs_network_git() -> None:
    stale = [
        relative
        for relative in sorted(EXCEPTIONS)
        if not network_git((PACKAGES / relative).read_text(encoding="utf-8"))
    ]
    assert not stale, f"{stale} no longer run network git: drop them from EXCEPTIONS"


@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        ('run_git(["clone", "--", url, dest])', True),
        ('args = ["fetch", "--quiet", "origin"]', True),
        ('run_git(("ls-remote", remote))', True),
        ('subprocess.run(["git", "push", "origin"])', True),
        ('run_git(["rev-parse", "HEAD"])', False),
        ('subprocess.run(["git", "status"])', False),
        ('kinds = ["fetched", "clone_url"]', False),
        ("run_git([verb, url])", False),
    ],
)
def test_the_detector(source: str, flagged: bool) -> None:
    assert bool(network_git(source)) is flagged
