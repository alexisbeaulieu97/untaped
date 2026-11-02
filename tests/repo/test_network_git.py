"""Network git goes through the git plugin: no other plugin clones, fetches, pulls or pushes.

The git plugin's repo store and remote queries carry what a host plugin
supplies (``GitHost`` credentials), untaped's proxy and the store's object
policy; a plugin that runs ``git clone`` itself gets none of that, so a
private repo on a host only a plugin answers for fails there alone. This
test reads every plugin's source but git's and flags an argv that runs a
git command reaching a remote (``clone``, ``fetch``, ``pull``, ``ls-remote``,
``push`` and the like): a list or tuple literal, as passed to ``run_git`` or
``subprocess``, or the arguments of a call to a function named for git. Its
exceptions only shrink.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from repo.support import PACKAGES

MESSAGE = (
    "network git goes through the git plugin (GitHost credentials, proxy, store): untaped_git.api"
)
VERBS = frozenset({"clone", "fetch", "fetch-pack", "send-pack", "pull", "ls-remote", "push"})
#: Two-word commands that reach a remote: ``git remote update``, ``git submodule update`` ...
PAIRS = frozenset({("remote", "update"), ("submodule", "update"), ("submodule", "add")})
#: git's own options that take the next word as their value (``git -C path fetch``).
VALUED = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"})
#: Modules allowed to run network git themselves, relative to ``packages/``, each with why.
EXCEPTIONS = {
    # Dotfiles' managed clones, until the dotfiles redesign moves them onto git.
    "untaped-dotfiles/src/untaped_dotfiles/git_repos.py",
    # The remote of the user's own checkout, asked with that checkout's own git config.
    "untaped-awx/src/untaped_awx/infrastructure/git_head.py",
    "untaped-awx/src/untaped_awx/infrastructure/git_source.py",
}


def network_git(source: str) -> list[int]:
    """The lines of ``source`` holding a git command that reaches a remote.

    A list or tuple literal is read as an argv: an optional executable first
    (``"git"`` or any non-literal, such as a name holding git's path), after
    any ``*base`` it splices in, then git's own options, then the command. A
    call to a function whose name holds ``git`` is read the same way from its
    positional arguments (``_git(cwd, "fetch")``).
    """
    lines = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.List, ast.Tuple)):
            words = node.elts
        elif isinstance(node, ast.Call) and "git" in _name(node.func).lower():
            words = node.args
        else:
            continue
        if _reaches_remote(words):
            lines.add(node.lineno)
    return sorted(lines)


def _reaches_remote(words: list[ast.expr]) -> bool:
    """Whether ``words``, read as a git argv, run a command that reaches a remote."""
    index = 0
    while index < len(words) and isinstance(words[index], ast.Starred):
        index += 1
    first = _word(words, index)
    if first == "git" or (index < len(words) and first is None):
        index += 1
    while (option := _word(words, index)) is not None and option.startswith("-"):
        index += 2 if option in VALUED else 1
    verb = _word(words, index)
    return verb in VERBS or (verb, _word(words, index + 1)) in PAIRS


def _word(words: list[ast.expr], index: int) -> str | None:
    """The string literal at ``index`` of ``words``, if that is what it holds."""
    if len(words) <= index:
        return None
    element = words[index]
    if isinstance(element, ast.Constant) and isinstance(element.value, str):
        return element.value
    return None


def _name(func: ast.expr) -> str:
    """The called function's own name (``run_git``, ``_git``, ``self._git``)."""
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


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
        ('_git(cwd, "fetch", "--quiet")', True),
        ('self._run_git("push", remote)', True),
        ('subprocess.run(["git", "-C", path, "-c", "a=b", "pull"])', True),
        ('subprocess.run([GIT, "fetch", "origin"])', True),
        ('run_git([*base, "pull"])', True),
        ('run_git(["remote", "update"])', True),
        ('run_git(["submodule", "update", "--init"])', True),
        ('run_git(["fetch-pack", url])', True),
        ('run_git(["remote", "get-url", "origin"])', False),
        ('_git(cwd, "status")', False),
        ('fetch(url, "clone")', False),
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
