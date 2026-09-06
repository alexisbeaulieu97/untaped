"""Structure tests for ApplyFile helper ownership."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
APPLY_FILE_MODULE = (
    REPO_ROOT / "src" / "untaped" / "capabilities" / "awx" / "application" / "apply_file.py"
)


def test_apply_file_does_not_own_ordering_or_prefetch_helpers() -> None:
    """ApplyFile should orchestrate; planning helpers belong in focused modules."""
    tree = ast.parse(APPLY_FILE_MODULE.read_text(encoding="utf-8"))
    defined_functions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}

    assert "_prefetch_plan" not in defined_functions
    assert "_topological_sort" not in defined_functions
    assert "_kahn_topological_order" not in defined_functions
