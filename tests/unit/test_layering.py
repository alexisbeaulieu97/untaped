"""Architectural-rule test: enforce DDD import direction across every domain.

The rule (per ``AGENTS.md`` 4-layer DDD section): ``application/`` modules
must not import their package's ``infrastructure`` namespace *at runtime*.
``TYPE_CHECKING`` imports are allowed because they don't create a runtime
edge.

This test discovers every domain package by globbing
``packages/*/src/<import_root>/application/``, walks the AST of every
``.py`` file in those directories, and asserts the rule for each. The
discovery is intentional: a new domain that follows the recipe in
``AGENTS.md`` is automatically covered with no test edits.

``untaped-core`` has no ``application/`` directory by design (it's a
flat shared kit), so it is excluded automatically.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES_DIR = REPO_ROOT / "packages"


def _discover_application_dirs() -> list[tuple[str, Path]]:
    """Return ``(import_root, application_dir)`` pairs for every domain.

    Globs ``packages/*/src/*/application/``; the parent directory's name
    is the package import root (``untaped_awx`` etc.). Returned sorted so
    test order is deterministic.
    """
    pairs: list[tuple[str, Path]] = []
    for app_dir in sorted(PACKAGES_DIR.glob("*/src/*/application")):
        if not app_dir.is_dir():
            continue
        import_root = app_dir.parent.name
        pairs.append((import_root, app_dir))
    return pairs


def _is_type_checking_guard(test: ast.expr) -> bool:
    return isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"


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


def _violations_in_file(import_root: str, py_file: Path, application_dir: Path) -> list[str]:
    infra_root = f"{import_root}.infrastructure"
    rel = py_file.relative_to(application_dir.parent)
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    found: list[str] = []
    for imp in _runtime_imports(tree):
        if isinstance(imp, ast.Import):
            bad = [alias.name for alias in imp.names if alias.name.startswith(infra_root)]
            if bad:
                found.append(f"{rel}:{imp.lineno} imports {', '.join(bad)}")
        elif imp.level > 0:
            # Relative import (`from ..infrastructure...`). Resolve against
            # the application/ package: any non-zero level pointing into a
            # sibling `infrastructure` package counts.
            module = imp.module or ""
            if module.startswith("infrastructure") or "infrastructure" in module:
                found.append(f"{rel}:{imp.lineno} imports {'.' * imp.level}{module}")
        elif imp.module and imp.module.startswith(infra_root):
            found.append(f"{rel}:{imp.lineno} imports {imp.module}")
    return found


@pytest.mark.parametrize(
    ("import_root", "application_dir"),
    _discover_application_dirs(),
    ids=lambda value: value if isinstance(value, str) else value.parent.name,
)
def test_application_does_not_import_infrastructure_at_runtime(
    import_root: str, application_dir: Path
) -> None:
    violations: list[str] = []
    for py_file in sorted(application_dir.glob("*.py")):
        violations.extend(_violations_in_file(import_root, py_file, application_dir))

    assert not violations, (
        f"{import_root}/application must not import {import_root}.infrastructure "
        "at runtime (TYPE_CHECKING imports are fine):\n  " + "\n  ".join(violations)
    )


def test_layering_test_discovers_every_domain() -> None:
    """Sanity check: the discovery glob actually finds the expected packages.

    If a new domain is added without an ``application/`` directory, this
    test fails fast — useful guard against silently skipping a package.
    """
    found_roots = {root for root, _ in _discover_application_dirs()}
    assert found_roots == {
        "untaped_awx",
        "untaped_config",
        "untaped_github",
        "untaped_profile",
        "untaped_workspace",
    }, f"unexpected domain set: {sorted(found_roots)}"
