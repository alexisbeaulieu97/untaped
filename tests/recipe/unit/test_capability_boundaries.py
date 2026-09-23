"""Import boundaries for the recipe built-in capability.

The recipe capability must not import another capability's implementation
modules (``untaped.capabilities.<name>.*`` for ``<name> != "recipe"``). The
engine used to ship as a separate ``untaped_recipe`` package composed through
``ToolSpec``/``run_tool``; nothing may import that package or those retired
composition helpers any more.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "untaped" / "capabilities" / "recipe"
TESTS_ROOT = REPO_ROOT / "tests" / "recipe"


def _runtime_imports(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    return [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]


def _capability_targets(py_file: Path) -> list[str]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    found: list[str] = []
    for imp in _runtime_imports(tree):
        if isinstance(imp, ast.Import):
            found.extend(alias.name for alias in imp.names if alias.name.startswith("untaped."))
        elif imp.level == 0 and imp.module and imp.module.startswith("untaped."):
            found.append(imp.module)
    return found


def test_recipe_imports_no_sibling_capability() -> None:
    # `untaped.capabilities.registry` is the composition kernel (home of
    # CapabilitySpec/SkillAsset), not a sibling capability implementation.
    violations: list[str] = []
    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        for target in _capability_targets(py_file):
            rest = target[len("untaped.") :]
            if rest == "capabilities" or rest.startswith("capabilities."):
                parts = rest.split(".")
                if len(parts) >= 2 and parts[1] not in ("recipe", "registry"):
                    violations.append(f"{py_file.relative_to(REPO_ROOT)} imports {target}")
    assert not violations, (
        "recipe capability must not import sibling capabilities:\n  " + "\n  ".join(violations)
    )


def test_no_legacy_package_imports_anywhere() -> None:
    violations: list[str] = []
    roots = [REPO_ROOT / "src", REPO_ROOT / "tests"]
    for root in roots:
        for py_file in sorted(root.rglob("*.py")):
            text = py_file.read_text(encoding="utf-8")
            tree = ast.parse(text)
            for imp in _runtime_imports(tree):
                names: list[str] = []
                if isinstance(imp, ast.Import):
                    names = [alias.name for alias in imp.names]
                elif imp.level == 0 and imp.module:
                    names = [imp.module]
                for name in names:
                    if name == "untaped_recipe" or name.startswith("untaped_recipe."):
                        violations.append(f"{py_file.relative_to(REPO_ROOT)} imports {name}")
    assert not violations, (
        "no legacy untaped_recipe package imports may survive:\n  " + "\n  ".join(violations)
    )


def test_no_retired_composition_imports_in_recipe() -> None:
    violations: list[str] = []
    for py_file in sorted([*SRC_ROOT.rglob("*.py"), *TESTS_ROOT.rglob("*.py")]):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in {
                "untaped.api",
                "untaped.tool",
                "untaped.run",
            }:
                bad = {
                    alias.name
                    for alias in node.names
                    if alias.name in {"ToolSpec", "register_tool", "build_tool_app", "run_tool"}
                }
                if bad:
                    violations.append(f"{py_file.relative_to(REPO_ROOT)} imports {sorted(bad)}")
    assert not violations, (
        "recipe tree must not use retired ToolSpec composition:\n  " + "\n  ".join(violations)
    )
