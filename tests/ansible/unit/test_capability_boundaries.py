"""Kernel-only import boundaries for the ansible built-in (Wave 2 slice 4, §6(b)).

The ansible capability consumes GitHub behavior ONLY through the reviewed
closed API (:mod:`untaped.capabilities.github.ansible`, import-plan
amendment 1) — never through sibling implementation modules, and never
through the sixteen ``untaped.capability_api`` provider helpers. No
``untaped-ansible`` standalone remnant (package imports, console-script
wiring, ``ToolSpec``/``run_tool`` composition) may survive anywhere in
src or tests.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "untaped" / "capabilities" / "ansible"
TESTS_ROOT = REPO_ROOT / "tests" / "ansible"

#: The single sanctioned ansible→github import path (import-plan amendment 1).
GITHUB_API_MODULE = "untaped.capabilities.github.ansible"

#: The sixteen provider helpers that are NOT the inter-capability interface.
CAPABILITY_API_HELPERS = frozenset(
    {
        "ColumnsOption",
        "ConfigError",
        "FormatOption",
        "StateCollection",
        "UiContext",
        "UntapedError",
        "app_context",
        "create_app",
        "echo",
        "emit",
        "finish",
        "first_validation_error",
        "get_config_section",
        "raise_usage",
        "read_identifiers",
        "report_errors",
    }
)


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


def test_ansible_imports_only_closed_github_api() -> None:
    # `untaped.capabilities.registry` is the composition kernel (home of
    # CapabilitySpec/SkillAsset), not a sibling capability implementation.
    violations: list[str] = []
    for py_file in sorted([*SRC_ROOT.rglob("*.py"), *TESTS_ROOT.rglob("*.py")]):
        for target in _capability_targets(py_file):
            rest = target[len("untaped.") :]
            if rest == "capabilities" or rest.startswith("capabilities."):
                parts = rest.split(".")
                if (
                    len(parts) >= 2
                    and parts[1] not in ("ansible", "registry")
                    and target != GITHUB_API_MODULE
                ):
                    violations.append(f"{py_file.relative_to(REPO_ROOT)} imports {target}")
    assert not violations, (
        "ansible capability must consume github ONLY through "
        f"{GITHUB_API_MODULE} (§6(b), amendment 1):\n  " + "\n  ".join(violations)
    )


def test_ansible_uses_no_capability_api_helpers() -> None:
    violations: list[str] = []
    for py_file in sorted([*SRC_ROOT.rglob("*.py"), *TESTS_ROOT.rglob("*.py")]):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "untaped.capability_api":
                bad = {alias.name for alias in node.names} & set(CAPABILITY_API_HELPERS)
                if bad:
                    violations.append(f"{py_file.relative_to(REPO_ROOT)} imports {sorted(bad)}")
    assert not violations, (
        "ansible tree must not use the sixteen capability_api helpers "
        "(not the inter-capability interface):\n  " + "\n  ".join(violations)
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
                    if name == "untaped_ansible" or name.startswith("untaped_ansible."):
                        violations.append(f"{py_file.relative_to(REPO_ROOT)} imports {name}")
    assert not violations, "no untaped-ansible standalone imports may survive:\n  " + "\n  ".join(
        violations
    )


def test_no_standalone_composition_imports_in_ansible() -> None:
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
        "ansible tree must not use retired ToolSpec composition (§9 gate 2):\n  "
        + "\n  ".join(violations)
    )


def test_no_standalone_console_script() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    scripts = data["project"].get("scripts", {})
    assert "untaped-ansible" not in scripts
    assert scripts.get("untaped") == "untaped.__main__:main"
