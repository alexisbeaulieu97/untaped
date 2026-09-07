"""Kernel-only import boundaries for the orchestration built-in (Wave 2 slice 6, §6(b)).

The orchestration capability MUST NOT import another capability's
implementation modules (``untaped.capabilities.<name>.*`` for
``<name> != "orchestration"``), and no ``untaped-orchestration``
standalone remnant (package imports, console-script wiring,
``ToolSpec``/``run_tool`` composition) may survive anywhere in src or tests.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "untaped" / "capabilities" / "orchestration"
TESTS_ROOT = REPO_ROOT / "tests" / "orchestration"


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


def test_orchestration_imports_no_sibling_capability() -> None:
    # `untaped.capabilities.registry` is the composition kernel (home of
    # CapabilitySpec/SkillAsset), not a sibling capability implementation.
    violations: list[str] = []
    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        for target in _capability_targets(py_file):
            rest = target[len("untaped.") :]
            if rest == "capabilities" or rest.startswith("capabilities."):
                parts = rest.split(".")
                if len(parts) >= 2 and parts[1] not in ("orchestration", "registry"):
                    violations.append(f"{py_file.relative_to(REPO_ROOT)} imports {target}")
    assert not violations, (
        "orchestration capability must not import sibling capabilities (§6(b)):\n  "
        + "\n  ".join(violations)
    )


def test_no_standalone_package_imports_anywhere() -> None:
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
                    if name == "untaped_orchestration" or name.startswith("untaped_orchestration."):
                        violations.append(f"{py_file.relative_to(REPO_ROOT)} imports {name}")
    assert not violations, (
        "no untaped-orchestration standalone imports may survive:\n  " + "\n  ".join(violations)
    )


def test_no_standalone_composition_imports_in_orchestration() -> None:
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
        "orchestration tree must not use retired ToolSpec composition (§9 gate 2):\n  "
        + "\n  ".join(violations)
    )


def test_no_standalone_console_script() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    scripts = data["project"].get("scripts", {})
    assert "untaped-orchestration" not in scripts
    assert scripts.get("untaped") == "untaped.__main__:main"
