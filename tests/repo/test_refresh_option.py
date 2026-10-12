"""A cache's ``--refresh``/``--cached`` comes from ``refresh_option(CACHE)``, never by hand.

``refresh_option`` writes the help from the cache's declaration, so the
setting it names and its default cannot drift from the code, and every
command says the directive the same way. A hand-declared ``Parameter``
spelling ``--refresh`` or ``--cached`` (as ``name`` or ``negative``) in a
shipped module fails here. The exemptions are the commands not on a
``Cache`` yet; each names the PR that moves it, and
:func:`test_the_exemptions_are_still_needed` fails once one is gone, so the
list shrinks with them.
"""

from __future__ import annotations

import ast
from pathlib import Path

from repo.support import PACKAGES, REPO_ROOT

FLAGS = frozenset({"--refresh", "--cached"})
#: ``(module, flag)`` → when it moves onto ``refresh_option``.
EXEMPT = {
    ("untaped_ansible/cli/graph_commands.py", "--refresh"): "until C (#537)",
    ("untaped_ansible/cli/graph_commands.py", "--cached"): "until C (#537)",
    ("untaped_github/cli/sweep_commands.py", "--refresh"): "until D (#582)",
    ("untaped_github/cli/sweep_commands.py", "--cached"): "until D (#582)",
    ("untaped_github/cli/cache_commands.py", "--refresh"): "until D (#582)",
}


def _spelled(node: ast.expr) -> set[str]:
    """The flags a ``name=``/``negative=`` value spells (a string, or a tuple or list of them)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Tuple | ast.List):
        return {each for item in node.elts for each in _spelled(item)}
    return set()


def hand_declared(source: str) -> list[tuple[int, str]]:
    """``(line, flag)`` of each ``Parameter(...)`` in ``source`` declaring a directive flag."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name != "Parameter":
            continue
        for keyword in node.keywords:
            if keyword.arg in ("name", "negative"):
                found += [(node.lineno, flag) for flag in sorted(_spelled(keyword.value) & FLAGS)]
        if node.args:
            found += [(node.lineno, flag) for flag in sorted(_spelled(node.args[0]) & FLAGS)]
    return sorted(found)


def _found() -> dict[tuple[str, str], list[int]]:
    roots = [*PACKAGES.glob("*/src"), *(REPO_ROOT / "examples").glob("*/src")]
    found: dict[tuple[str, str], list[int]] = {}
    for root in roots:
        for path in sorted(Path(root).rglob("*.py")):
            module = path.relative_to(root).as_posix()
            if module == "untaped/cache.py":  # refresh_option itself
                continue
            for line, flag in hand_declared(path.read_text(encoding="utf-8")):
                found.setdefault((module, flag), []).append(line)
    return found


def test_no_command_declares_the_directive_by_hand() -> None:
    stray = [
        f"{module}:{line} declares {flag}"
        for (module, flag), lines in _found().items()
        if (module, flag) not in EXEMPT
        for line in lines
    ]
    assert not stray, (
        f"{stray}: declare a cache's directive as "
        "`Annotated[bool | None, refresh_option(CACHE)] = None` (untaped.sdk), so its help "
        "names the cache's setting and default; see docs/reference/conventions.md#options"
    )


def test_the_exemptions_are_still_needed() -> None:
    gone = sorted(set(EXEMPT) - set(_found()))
    assert not gone, f"{gone} no longer declare the flag by hand: drop them from EXEMPT"


def test_the_finder_flags_hand_declared_directives_only() -> None:
    bad = """
from cyclopts import Parameter
a: Annotated[bool | None, Parameter(name="--refresh", negative="--cached")] = None
b: Annotated[bool, Parameter(name=("--cached", "-C"), negative="")] = False
c: Annotated[bool, cyclopts.Parameter("--refresh")] = False
"""
    assert hand_declared(bad) == [
        (3, "--cached"),
        (3, "--refresh"),
        (4, "--cached"),
        (5, "--refresh"),
    ]
    good = """
a: Annotated[bool | None, refresh_option(INDEX)] = None
b: Annotated[bool, Parameter(name="--refreshed", negative="")] = False
c = Parameter(help="--refresh checks upstream now")
"""
    assert hand_declared(good) == []
