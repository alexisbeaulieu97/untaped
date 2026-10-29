"""A cache's ``--refresh``/``--cached`` comes from a ``Refresh(CACHE)`` field, never by hand.

``Refresh`` writes the help from the caches' declarations, so the setting it
names and its default cannot drift from the code, and every command says the
directive the same way. A hand-declared ``Parameter`` spelling ``--refresh``
or ``--cached`` (as ``name`` or ``negative``) in a shipped module fails here.
"""

from __future__ import annotations

import ast
from pathlib import Path

from repo.support import PACKAGES, REPO_ROOT

FLAGS = frozenset({"--refresh", "--cached"})


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
            for line, flag in hand_declared(path.read_text(encoding="utf-8")):
                found.setdefault((module, flag), []).append(line)
    return found


def test_no_command_declares_the_directive_by_hand() -> None:
    stray = [
        f"{module}:{line} declares {flag}"
        for (module, flag), lines in _found().items()
        for line in lines
    ]
    assert not stray, (
        f"{stray}: declare a cache's directive as "
        "a `refresh: Refresh = Refresh(CACHE)` field (untaped.sdk), so its help names the "
        "cache's setting and default; see docs/caches.md"
    )


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
a: Refresh = Refresh(INDEX)
b: Annotated[bool, Parameter(name="--refreshed", negative="")] = False
c = Parameter(help="--refresh checks upstream now")
"""
    assert hand_declared(good) == []
