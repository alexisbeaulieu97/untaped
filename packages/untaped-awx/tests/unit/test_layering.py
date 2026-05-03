"""Architectural-rule tests: enforce DDD import direction at the file level.

The rule (per ``AGENTS.md`` 4-layer DDD section): ``application/`` modules
must not import anything from ``untaped_awx.infrastructure`` *at runtime*.
``TYPE_CHECKING`` imports are allowed because they don't create a runtime
edge.

This test walks the AST of every ``application/*.py`` file and asserts the
rule. Cheaper and more targeted than wiring up ``import-linter`` for one
constraint; if the rule list grows, switch tools.
"""

from __future__ import annotations

import ast
from pathlib import Path

import untaped_awx

APPLICATION_DIR = Path(untaped_awx.__file__).parent / "application"


def _runtime_imports(tree: ast.Module) -> list[ast.ImportFrom]:
    """Return ``ImportFrom`` nodes that execute at runtime.

    Skips imports inside ``if TYPE_CHECKING:`` blocks — those are evaluated
    only by type checkers, never at runtime, so they don't violate the
    layering contract.
    """
    typecheck_block_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking_guard(node.test):
            for child in ast.walk(node):
                if hasattr(child, "lineno"):
                    typecheck_block_lines.add(child.lineno)

    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.lineno not in typecheck_block_lines
    ]


def _is_type_checking_guard(test: ast.expr) -> bool:
    return isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"


def test_application_does_not_import_infrastructure_at_runtime() -> None:
    violations: list[str] = []
    for py_file in sorted(APPLICATION_DIR.glob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for imp in _runtime_imports(tree):
            if imp.module and imp.module.startswith("untaped_awx.infrastructure"):
                violations.append(
                    f"{py_file.relative_to(APPLICATION_DIR.parent)}:{imp.lineno} "
                    f"imports {imp.module}"
                )

    assert not violations, (
        "application/ must not import infrastructure/ at runtime "
        "(TYPE_CHECKING imports are fine):\n  " + "\n  ".join(violations)
    )
