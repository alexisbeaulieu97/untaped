"""Architecture guards for plugin boundaries in core."""

from __future__ import annotations

import ast
from pathlib import Path

CORE_SRC = Path(__file__).resolve().parents[2] / "src" / "untaped"
EXTERNAL_PLUGIN_MODULES = frozenset(
    {
        "untaped_awx",
        "untaped_github",
        "untaped_profile",
        "untaped_workspace",
    }
)


def test_core_does_not_statically_import_external_plugins() -> None:
    violations: list[str] = []
    for py_file in sorted(CORE_SRC.rglob("*.py")):
        rel = py_file.relative_to(CORE_SRC.parent)
        tree = ast.parse(py_file.read_text())
        for imp in ast.walk(tree):
            if isinstance(imp, ast.Import):
                for alias in imp.names:
                    root = alias.name.split(".", maxsplit=1)[0]
                    if root in EXTERNAL_PLUGIN_MODULES:
                        violations.append(f"{rel}:{imp.lineno} imports {alias.name}")
            elif isinstance(imp, ast.ImportFrom) and imp.module:
                root = imp.module.split(".", maxsplit=1)[0]
                if root in EXTERNAL_PLUGIN_MODULES:
                    violations.append(f"{rel}:{imp.lineno} imports {imp.module}")

    assert not violations, (
        "untaped core must not statically import external plugin modules; "
        "plugins are loaded through entry points:\n  " + "\n  ".join(violations)
    )
