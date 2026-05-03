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


def _runtime_imports(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    """Return ``Import`` / ``ImportFrom`` nodes that execute at runtime.

    Skips imports inside ``if TYPE_CHECKING:`` blocks — those are evaluated
    only by type checkers, never at runtime, so they don't violate the
    layering contract. Includes both ``import x.y.z`` (``ast.Import``) and
    ``from x.y.z import ...`` (``ast.ImportFrom``) so neither form can
    bypass the rule.
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
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and node.lineno not in typecheck_block_lines
    ]


def _is_type_checking_guard(test: ast.expr) -> bool:
    return isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"


def test_application_does_not_import_infrastructure_at_runtime() -> None:
    violations: list[str] = []
    for py_file in sorted(APPLICATION_DIR.glob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        rel = py_file.relative_to(APPLICATION_DIR.parent)
        for imp in _runtime_imports(tree):
            if isinstance(imp, ast.Import):
                bad = [
                    alias.name
                    for alias in imp.names
                    if alias.name.startswith("untaped_awx.infrastructure")
                ]
                if bad:
                    violations.append(f"{rel}:{imp.lineno} imports {', '.join(bad)}")
            elif imp.level > 0:
                # Relative import (`from ..infrastructure...`). Resolve against
                # the application/ package: any non-zero level pointing into a
                # sibling `infrastructure` package counts.
                module = imp.module or ""
                if module.startswith("infrastructure") or "infrastructure" in module:
                    violations.append(f"{rel}:{imp.lineno} imports {'.' * imp.level}{module}")
            elif imp.module and imp.module.startswith("untaped_awx.infrastructure"):
                violations.append(f"{rel}:{imp.lineno} imports {imp.module}")

    assert not violations, (
        "application/ must not import infrastructure/ at runtime "
        "(TYPE_CHECKING imports are fine):\n  " + "\n  ".join(violations)
    )
