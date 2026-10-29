"""Lint's design rules no linter checks, read from the source with ``ast``.

- No ``Linter`` method, nor one of a class built on ``Linter`` or ``Rules``,
  is ``@cached``: a run must see the rules and files as they are now.
- ``Conventions`` is the engine's own code, no contract's provider (so no
  plugin class fills a contract with it): it composes, which no contract
  method may.
- Within ``untaped_lint`` only ``providers/`` imports ``check_plugin``, and
  only ``domain/glob.py`` uses ``fnmatch`` or ``glob.translate``: one door
  to core's checks, one glob.
- Once a release carries ``untaped[lint]``, an example's ``untaped[lint]``
  range leaves out the releases without it.
"""

from __future__ import annotations

import ast
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

from repo.support import PACKAGES, REPO_ROOT
from untaped.sdk.contracts._declare import contract_of
from untaped_lint.providers.conventions import Conventions

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


def test_conventions_is_no_provider() -> None:
    assert contract_of(Conventions) is None


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
    users = _lint_uses("untaped.sdk.testing.check_plugin")
    assert users, "the scan finds no import of check_plugin at all"
    assert [path for path in users if not path.startswith("providers/")] == []


def test_only_lint_glob_module_uses_fnmatch_or_glob_translate() -> None:
    users = _lint_uses("fnmatch", "glob.translate")
    assert set(users) == {"domain/glob.py"}


#: The first release with the ``lint`` extra.
LINT_SINCE = Version("11.1.0")


def test_the_examples_lint_range_leaves_out_releases_without_lint() -> None:
    core = PACKAGES / "untaped" / "pyproject.toml"
    if Version(tomllib.loads(core.read_text(encoding="utf-8"))["project"]["version"]) < LINT_SINCE:
        pytest.skip("no release carries the lint extra yet, so the floor can't rise")
    stale = []
    for path in sorted((REPO_ROOT / "examples").glob("*/pyproject.toml")):
        groups = tomllib.loads(path.read_text(encoding="utf-8")).get("dependency-groups", {})
        for line in (each for lines in groups.values() for each in lines if isinstance(each, str)):
            requirement = Requirement(line)
            if "lint" in requirement.extras and requirement.specifier.contains("11.0.0"):
                stale.append(f"{path.relative_to(REPO_ROOT)}: {line}")
    assert not stale, "raise the floor to untaped[lint]>=11.1:\n" + "\n".join(stale)
