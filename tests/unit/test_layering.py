"""Architecture guard tests for the workspace plugin's DDD layers."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "untaped_workspace"


def _is_type_checking_guard(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return (
            test.attr == "TYPE_CHECKING"
            and isinstance(test.value, ast.Name)
            and test.value.id == "typing"
        )
    return False


def _typecheck_block_lines(tree: ast.Module) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking_guard(node.test):
            for stmt in node.body:
                for child in ast.walk(stmt):
                    if hasattr(child, "lineno"):
                        lines.add(child.lineno)
    return lines


def _runtime_imports(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    typecheck_block_lines = _typecheck_block_lines(tree)
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and node.lineno not in typecheck_block_lines
    ]


def _violations_in_file(
    py_file: Path,
    source_dir: Path,
    forbidden_subpackage: str,
) -> list[str]:
    forbidden_root = f"untaped_workspace.{forbidden_subpackage}"
    rel = py_file.relative_to(SRC_ROOT)
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    found: list[str] = []
    for imp in _runtime_imports(tree):
        if isinstance(imp, ast.Import):
            bad = [
                alias.name
                for alias in imp.names
                if alias.name == forbidden_root or alias.name.startswith(f"{forbidden_root}.")
            ]
            if bad:
                found.append(f"{rel}:{imp.lineno} imports {', '.join(bad)}")
        elif imp.level > 0:
            module = imp.module or ""
            if module == forbidden_subpackage or module.startswith(f"{forbidden_subpackage}."):
                found.append(f"{rel}:{imp.lineno} imports {'.' * imp.level}{module}")
        elif imp.module and (
            imp.module == forbidden_root or imp.module.startswith(f"{forbidden_root}.")
        ):
            found.append(f"{rel}:{imp.lineno} imports {imp.module}")
    return found


@pytest.mark.parametrize(
    ("source_dir", "forbidden_subpackage"),
    [
        (SRC_ROOT / "application", "infrastructure"),
        (SRC_ROOT / "infrastructure", "application"),
    ],
    ids=["application->infrastructure", "infrastructure->application"],
)
def test_layers_do_not_import_forbidden_siblings(
    source_dir: Path,
    forbidden_subpackage: str,
) -> None:
    violations: list[str] = []
    for py_file in sorted(source_dir.rglob("*.py")):
        violations.extend(_violations_in_file(py_file, source_dir, forbidden_subpackage))

    assert not violations, (
        f"{source_dir.relative_to(SRC_ROOT)} must not import "
        f"untaped_workspace.{forbidden_subpackage} at runtime "
        "(TYPE_CHECKING imports are fine):\n  " + "\n  ".join(violations)
    )


def test_infrastructure_does_not_read_global_settings() -> None:
    violations: list[str] = []

    for py_file in sorted((SRC_ROOT / "infrastructure").rglob("*.py")):
        violations.extend(_settings_violations_in_file(py_file, SRC_ROOT))

    assert not violations, (
        "Infrastructure adapters must receive narrowed settings from the CLI composition root, "
        "not read the global settings aggregate:\n  " + "\n  ".join(violations)
    )


def _settings_violations_in_file(py_file: Path, src_dir: Path) -> list[str]:  # noqa: C901
    forbidden_names = frozenset({"Settings", "get_settings"})
    rel = py_file.relative_to(src_dir)
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    typecheck_lines = _typecheck_block_lines(tree)
    found: list[str] = []

    top_aliases: set[str] = set()
    sub_aliases: set[str] = set()

    for imp in _runtime_imports(tree):
        if isinstance(imp, ast.ImportFrom) and imp.module in {"untaped", "untaped.settings"}:
            bad = sorted({alias.name for alias in imp.names if alias.name in forbidden_names})
            if bad:
                found.append(f"{rel}:{imp.lineno} imports {', '.join(bad)} from {imp.module}")
            if imp.module == "untaped":
                for alias in imp.names:
                    if alias.name == "settings":
                        sub_aliases.add(alias.asname or "settings")
        elif isinstance(imp, ast.Import):
            for alias in imp.names:
                if alias.name == "untaped":
                    top_aliases.add(alias.asname or "untaped")
                elif alias.name == "untaped.settings":
                    if alias.asname:
                        sub_aliases.add(alias.asname)
                    else:
                        top_aliases.add("untaped")

    if top_aliases or sub_aliases:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if node.lineno in typecheck_lines:
                continue
            if node.attr not in forbidden_names:
                continue
            if isinstance(node.value, ast.Name):
                name = node.value.id
                if name in top_aliases or name in sub_aliases:
                    found.append(f"{rel}:{node.lineno} reads {name}.{node.attr}")
            elif isinstance(node.value, ast.Attribute) and node.value.attr == "settings":
                inner = node.value.value
                if isinstance(inner, ast.Name) and inner.id in top_aliases:
                    found.append(f"{rel}:{node.lineno} reads {inner.id}.settings.{node.attr}")

    return found


_BYPASS_SOURCES: list[tuple[str, str]] = [
    (
        "import-alias-direct",
        "import untaped as core\ndef f() -> None:\n    core.get_settings()\n",
    ),
    (
        "import-alias-class",
        "import untaped as core\ndef f() -> None:\n    core.Settings()\n",
    ),
    (
        "from-import-submodule",
        "from untaped import settings\ndef f() -> None:\n    settings.get_settings()\n",
    ),
    (
        "from-import-submodule-aliased",
        "from untaped import settings as cfg\ndef f() -> None:\n    cfg.get_settings()\n",
    ),
    (
        "import-submodule-chained",
        "import untaped.settings\ndef f() -> None:\n    untaped.settings.get_settings()\n",
    ),
    (
        "import-submodule-aliased",
        "import untaped.settings as s\ndef f() -> None:\n    s.Settings()\n",
    ),
    (
        "direct-import",
        "from untaped import get_settings\ndef f() -> None:\n    get_settings()\n",
    ),
    (
        "type-checking-else-branch",
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from untaped import HttpSettings\n"
        "else:\n"
        "    from untaped import get_settings\n"
        "def f() -> None:\n"
        "    get_settings()\n",
    ),
]


@pytest.mark.parametrize(
    ("label", "source"),
    _BYPASS_SOURCES,
    ids=[lbl for lbl, _ in _BYPASS_SOURCES],
)
def test_settings_violation_helper_catches_alias_bypasses(
    tmp_path: Path, label: str, source: str
) -> None:
    src_dir = tmp_path / "untaped_fake"
    infra_dir = src_dir / "infrastructure"
    infra_dir.mkdir(parents=True)
    py_file = infra_dir / "client.py"
    py_file.write_text(source, encoding="utf-8")

    violations = _settings_violations_in_file(py_file, src_dir)

    assert violations, f"expected {label} pattern to be flagged"


def test_settings_violation_helper_ignores_legitimate_imports(tmp_path: Path) -> None:
    src_dir = tmp_path / "untaped_fake"
    infra_dir = src_dir / "infrastructure"
    infra_dir.mkdir(parents=True)
    py_file = infra_dir / "client.py"
    py_file.write_text(
        "from untaped import ConfigError, HttpClient, HttpSettings\n"
        "import untaped\n"
        "def f() -> None:\n"
        "    untaped.HttpClient(base_url='x')\n",
        encoding="utf-8",
    )

    assert _settings_violations_in_file(py_file, src_dir) == []
