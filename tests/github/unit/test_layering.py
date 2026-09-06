"""Architecture guard tests for the GitHub tool's DDD layers."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = REPO_ROOT / "src" / "untaped" / "capabilities" / "github"


def _runtime_imports(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    typecheck_lines = _typecheck_block_lines(tree)
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom)) and node.lineno not in typecheck_lines
    ]


def _typecheck_block_lines(tree: ast.Module) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking_guard(node.test):
            for stmt in node.body:
                for child in ast.walk(stmt):
                    if hasattr(child, "lineno"):
                        lines.add(child.lineno)
    return lines


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


def _violations_in_file(py_file: Path, forbidden_subpackage: str) -> list[str]:
    forbidden_root = f"untaped.capabilities.github.{forbidden_subpackage}"
    rel = py_file.relative_to(SRC_DIR)
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
    ("source_dir", "forbidden_subpackage"),
    [
        (SRC_DIR / "application", "infrastructure"),
        (SRC_DIR / "infrastructure", "application"),
    ],
    ids=["application-no-infrastructure", "infrastructure-no-application"],
)
def test_layers_do_not_import_forbidden_sibling(
    source_dir: Path, forbidden_subpackage: str
) -> None:
    violations: list[str] = []
    for py_file in sorted(source_dir.rglob("*.py")):
        violations.extend(_violations_in_file(py_file, forbidden_subpackage))

    assert not violations, "\n".join(violations)


def test_infrastructure_does_not_read_global_settings() -> None:
    violations: list[str] = []
    for py_file in sorted((SRC_DIR / "infrastructure").rglob("*.py")):
        rel = py_file.relative_to(SRC_DIR)
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for imp in _runtime_imports(tree):
            if isinstance(imp, ast.ImportFrom) and imp.module in {
                "untaped",
                "untaped.api",
                "untaped.settings",
            }:
                bad = sorted(
                    alias.name
                    for alias in imp.names
                    if alias.name in {"Settings", "get_settings", "get_core_settings"}
                )
                if bad:
                    violations.append(f"{rel}:{imp.lineno} imports {', '.join(bad)}")

    assert not violations, "\n".join(violations)
