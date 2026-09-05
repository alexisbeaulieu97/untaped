"""Negative CI proof for the ToolSpec-era surface (spec §9 gate 3, Wave 1.6).

``ToolSpec``, ``register_tool``, ``build_tool_app``, and ``run_tool`` stay
operative in the legacy paths until retirement completes, but the v4
composition surface must never touch them. This suite fails the build
when any file under the v4 runtime paths defines, imports, exports, or
aliases one of the four retired names — including ``as``-import aliases
in either direction and wrapper assignments.

Docstrings and comments may still name them (prose is not composition).
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "untaped"

RETIRED = frozenset({"ToolSpec", "register_tool", "build_tool_app", "run_tool"})

V4_PATHS = (
    SRC_ROOT / "capabilities",
    SRC_ROOT / "management",
    SRC_ROOT / "capability_api.py",
    SRC_ROOT / "bootstrap.py",
    SRC_ROOT / "__main__.py",
)


def legacy_violations(paths: Sequence[Path] = V4_PATHS) -> list[str]:
    """Flag definitions, imports, exports, and aliases of retired names."""
    violations: list[str] = []
    for path in paths:
        files = sorted(path.rglob("*.py")) if path.is_dir() else [path]
        for py_file in files:
            if py_file.is_file():
                violations.extend(_file_violations(py_file))
    return violations


def _file_violations(py_file: Path) -> list[str]:
    found: list[str] = []
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in RETIRED:
                found.append(f"{_rel(py_file)}:{node.lineno}: defines retired {node.name!r}")
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in RETIRED or (alias.asname or "") in RETIRED:
                    found.append(
                        f"{_rel(py_file)}:{node.lineno}: imports retired "
                        f"{alias.name!r} (as {alias.asname!r})"
                    )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if (alias.asname or "") in RETIRED:
                    found.append(
                        f"{_rel(py_file)}:{node.lineno}: aliases import to retired {alias.asname!r}"
                    )
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            found.extend(_assignment_violations(py_file, node))
    found.extend(_all_violations(py_file, tree))
    return found


def _assignment_violations(py_file: Path, node: ast.Assign | ast.AnnAssign) -> list[str]:
    found: list[str] = []
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        for name in _stored_names(target):
            if name in RETIRED:
                found.append(f"{_rel(py_file)}:{node.lineno}: binds retired name {name!r}")
    value = node.value
    is_name_alias = isinstance(value, ast.Name) and value.id in RETIRED
    is_attr_alias = isinstance(value, ast.Attribute) and value.attr in RETIRED
    if is_name_alias or is_attr_alias:
        found.append(
            f"{_rel(py_file)}:{node.lineno}: aliases retired "
            f"{getattr(value, 'id', getattr(value, 'attr', ''))!r}"
        )
    return found


def _stored_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Store):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for item in target.elts for name in _stored_names(item)]
    if isinstance(target, ast.Starred):
        return _stored_names(target.value)
    return []


def _all_violations(py_file: Path, tree: ast.Module) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
            )
            and isinstance(node.value, (ast.List, ast.Tuple))
        ):
            for element in node.value.elts:
                if isinstance(element, ast.Constant) and element.value in RETIRED:
                    found.append(
                        f"{_rel(py_file)}:{node.lineno}: exports retired "
                        f"{element.value!r} via __all__"
                    )
    return found


def _rel(py_file: Path) -> str:
    try:
        return str(py_file.relative_to(REPO_ROOT))
    except ValueError:
        return str(py_file)


# ── live-tree positive ───────────────────────────────────────────────────────


def test_v4_paths_define_import_export_or_alias_no_legacy_surface() -> None:
    assert legacy_violations() == []


def test_v4_paths_are_all_present() -> None:
    missing = [str(path) for path in V4_PATHS if not path.exists()]
    assert missing == []


# ── negatives (hermetic probes) ──────────────────────────────────────────────


def _probe(tmp_path: Path, text: str) -> list[str]:
    target = tmp_path / "probe.py"
    target.write_text(text, encoding="utf-8")
    return legacy_violations([target])


def test_definition_fails(tmp_path: Path) -> None:
    violations = _probe(tmp_path, "class ToolSpec:\n    pass\n")
    assert len(violations) == 1
    assert "defines retired 'ToolSpec'" in violations[0]


def test_function_definition_fails(tmp_path: Path) -> None:
    violations = _probe(tmp_path, "def run_tool(app, spec):\n    return app\n")
    assert len(violations) == 1
    assert "defines retired 'run_tool'" in violations[0]


def test_import_and_alias_directions_fail(tmp_path: Path) -> None:
    assert _probe(tmp_path, "from untaped.tool import ToolSpec\n")
    assert _probe(tmp_path, "from untaped.tool import ToolSpec as Spec\n")
    assert _probe(tmp_path, "from untaped.tool import Spec as run_tool\n")
    assert _probe(tmp_path, "import untaped.run as build_tool_app\n")


def test_export_and_assignment_alias_fail(tmp_path: Path) -> None:
    assert _probe(tmp_path, '__all__ = ["register_tool"]\n')
    assert _probe(tmp_path, "Spec = ToolSpec\n")
    assert _probe(tmp_path, "run_tool = None\n")


def test_prose_and_unrelated_code_pass(tmp_path: Path) -> None:
    text = (
        '"""Migrated away from ``run_tool`` composition."""\n'
        "NOTE = 'ToolSpec-era behavior'\n"
        "run_tools = []\n"
        "def run_tooling() -> None:\n"
        "    return None\n"
    )
    assert _probe(tmp_path, text) == []
