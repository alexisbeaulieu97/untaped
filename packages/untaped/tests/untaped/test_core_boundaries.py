"""Architecture guard: core imports no plugin package."""

from __future__ import annotations

import ast
from pathlib import Path

CORE_SRC = Path(__file__).resolve().parents[2] / "src" / "untaped"


def _imported(tree: ast.Module) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.append((node.lineno, node.module))
    return found


def test_core_imports_no_plugin_package() -> None:
    """Any top-level ``untaped_*`` module is a plugin's; a string naming one is not an import."""
    violations = [
        f"{path.relative_to(CORE_SRC.parent)}:{line} imports {name}"
        for path in sorted(CORE_SRC.rglob("*.py"))
        for line, name in _imported(ast.parse(path.read_text(encoding="utf-8")))
        if name.split(".", maxsplit=1)[0].startswith("untaped_")
    ]
    assert not violations, "untaped core must not import plugin packages:\n  " + "\n  ".join(
        violations
    )
