"""Lint's design rules no linter checks, read from the source with ``ast``.

- No ``Linter`` method, nor one of a class built on ``Linter`` or ``Rules``,
  is ``@cached``: a run must see the rules and files as they are now.
- ``Conventions`` is the engine's own code, offered in no ``PluginSpec(provides=...)``:
  it composes, which no contract method may.
- Within ``untaped_lint`` only ``providers/`` imports ``check_plugin``, and
  only ``domain/glob.py`` uses ``fnmatch`` or ``glob.translate``: one door
  to core's checks, one glob.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

from repo.support import PACKAGES, REPO_ROOT

LINT = PACKAGES / "untaped-lint" / "src" / "untaped_lint"


def _sources() -> Iterator[tuple[Path, ast.Module]]:
    roots = [*PACKAGES.glob("*/src"), *(REPO_ROOT / "examples").glob("*/src")]
    for root in sorted(roots):
        for path in sorted(root.rglob("*.py")):
            yield path, ast.parse(path.read_text(encoding="utf-8"))


def _name(node: ast.expr) -> str | None:
    """The last name of ``x``, ``a.x`` or a call of either."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else None


def _where(path: Path, node: ast.AST) -> str:
    return f"{path.relative_to(REPO_ROOT)}:{getattr(node, 'lineno', 0)}"


def test_no_linter_method_is_cached() -> None:
    classes = [
        (path, node)
        for path, tree in _sources()
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    ]
    linters = {"Linter", "Rules"}
    while True:  # every class built on one, however far down
        more = {
            node.name
            for _path, node in classes
            if any(_name(base) in linters for base in node.bases)
        }
        if more <= linters:
            break
        linters |= more
    cached = [
        _where(path, method)
        for path, node in classes
        if node.name in linters
        for method in node.body
        if isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef)
        and any(_name(each) == "cached" for each in method.decorator_list)
    ]
    assert not cached, f"@cached on a Linter method: {cached}"


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def _names(node: ast.AST, functions: dict[str, ast.FunctionDef], seen: set[str]) -> Iterator[str]:
    """Every name under ``node``, following references to the module's own functions."""
    for each in ast.walk(node):
        name = _name(each) if isinstance(each, ast.Name | ast.Attribute) else None
        if name is None:
            continue
        yield name
        if name in functions and name not in seen:
            seen.add(name)
            yield from _names(functions[name], functions, seen)


def test_conventions_is_offered_by_no_plugin() -> None:
    offered = []
    for path, tree in _sources():
        functions = _functions(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _name(node) == "PluginSpec"):
                continue
            for keyword in node.keywords:
                if keyword.arg == "provides" and "Conventions" in _names(
                    keyword.value, functions, set()
                ):
                    offered.append(_where(path, node))
    assert not offered, f"Conventions in PluginSpec(provides=...): {offered}"


def _imports(tree: ast.Module) -> Iterator[tuple[ast.AST, str]]:
    """``(node, dotted name)`` for each module or name imported, and each ``a.b`` attribute."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from ((node, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield node, node.module or ""
            yield from ((node, f"{node.module}.{alias.name}") for alias in node.names)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            yield node, f"{node.value.id}.{node.attr}"


def _lint_uses(*names: str) -> dict[str, list[ast.AST]]:
    found: dict[str, list[ast.AST]] = {}
    for path in sorted(LINT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node, name in _imports(tree):
            if any(name == each or name.endswith(f".{each}") for each in names):
                found.setdefault(path.relative_to(LINT).as_posix(), []).append(node)
    return found


def test_only_lint_providers_import_check_plugin() -> None:
    users = _lint_uses("untaped.testing.check_plugin")
    assert users, "the scan finds no import of check_plugin at all"
    assert [path for path in users if not path.startswith("providers/")] == []


def test_only_lint_glob_module_uses_fnmatch_or_glob_translate() -> None:
    users = _lint_uses("fnmatch", "glob.translate")
    assert set(users) == {"domain/glob.py"}
