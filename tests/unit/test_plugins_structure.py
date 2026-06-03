"""Structure tests for plugin command helper ownership."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGINS_MODULE = REPO_ROOT / "src" / "untaped" / "plugins.py"


def test_plugins_facade_does_not_own_uv_subprocess_execution() -> None:
    """The public `untaped.plugins` module should not own sync subprocess wiring."""
    tree = ast.parse(PLUGINS_MODULE.read_text(encoding="utf-8"))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.add(node.module)

    assert "subprocess" not in imported_modules
    assert "shlex" not in imported_modules


def test_plugins_facade_does_not_define_plugin_runtime_types() -> None:
    """Runtime registry types should live outside the command facade."""
    tree = ast.parse(PLUGINS_MODULE.read_text(encoding="utf-8"))
    defined_classes = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}

    assert "PluginRegistry" not in defined_classes
    assert "DiagnosticResult" not in defined_classes
    assert "UntapedPlugin" not in defined_classes
