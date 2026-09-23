"""Architecture guard tests for the recipe capability's layers.

``AGENTS.md`` fixes the import direction inside a capability:
``cli -> application -> domain`` and ``infrastructure -> domain``. These tests
enforce it at runtime-import level: ``domain/`` imports no other layer,
``application/`` reaches adapters only through ``application/ports.py``
protocols, and ``infrastructure/`` never imports ``application/`` or ``cli/``.
``TYPE_CHECKING`` imports are allowed because they create no runtime edge.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "untaped" / "capabilities" / "recipe"


def _is_type_checking_guard(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return (
            test.attr == "TYPE_CHECKING"
            and isinstance(test.value, ast.Name)
            and test.value.id == "typing"
        )
    return False


def _typecheck_block_lines(tree: ast.Module) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking_guard(node.test):
            for stmt in node.body:
                for child in ast.walk(stmt):
                    if hasattr(child, "lineno"):
                        lines.add(child.lineno)
    return lines


def _runtime_imports(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    typecheck_block_lines = _typecheck_block_lines(tree)
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and node.lineno not in typecheck_block_lines
    ]


def _violations_in_file(
    py_file: Path,
    source_dir: Path,
    forbidden_subpackage: str,
) -> list[str]:
    forbidden_root = f"untaped.capabilities.recipe.{forbidden_subpackage}"
    rel = py_file.relative_to(SRC_ROOT)
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    found: list[str] = []
    for imp in _runtime_imports(tree):
        if isinstance(imp, ast.Import):
            bad = [
                alias.name
                for alias in imp.names
                if alias.name == forbidden_root or alias.name.startswith(f"{forbidden_root}.")
            ]
            if bad:
                found.append(f"{rel}:{imp.lineno} imports {', '.join(bad)}")
        elif imp.level > 0:
            module = imp.module or ""
            if module == forbidden_subpackage or module.startswith(f"{forbidden_subpackage}."):
                found.append(f"{rel}:{imp.lineno} imports {'.' * imp.level}{module}")
        elif imp.module and (
            imp.module == forbidden_root or imp.module.startswith(f"{forbidden_root}.")
        ):
            found.append(f"{rel}:{imp.lineno} imports {imp.module}")
    return found


@pytest.mark.parametrize(
    ("layer", "forbidden_subpackage"),
    [
        ("domain", "application"),
        ("domain", "infrastructure"),
        ("domain", "cli"),
        ("application", "infrastructure"),
        ("application", "cli"),
        ("infrastructure", "application"),
        ("infrastructure", "cli"),
    ],
    ids=lambda value: value,
)
def test_layer_does_not_import_forbidden_layer(layer: str, forbidden_subpackage: str) -> None:
    violations: list[str] = []
    for py_file in sorted((SRC_ROOT / layer).rglob("*.py")):
        violations.extend(_violations_in_file(py_file, SRC_ROOT / layer, forbidden_subpackage))

    assert not violations, (
        f"{layer} must not import untaped.capabilities.recipe.{forbidden_subpackage} "
        "at runtime (TYPE_CHECKING imports are fine):\n  " + "\n  ".join(violations)
    )
