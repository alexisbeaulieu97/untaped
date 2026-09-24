"""Import-boundary lint: layers inside each capability point one way.

``cli → application → domain`` and ``infrastructure → domain``
(``docs/conventions.md``). A *runtime* import (``TYPE_CHECKING`` blocks are
exempt) that crosses a layer the wrong way is flagged as
``<file>::layer::<from layer> -> <imported module>``:

- ``domain`` imports nothing from ``application``, ``infrastructure`` or ``cli``;
- ``application`` imports nothing from ``infrastructure`` or ``cli``;
- ``infrastructure`` imports nothing from ``cli`` and ``application`` only
  under ``TYPE_CHECKING`` (adapters satisfy ports structurally);
- none of them resolves settings (``app_context``, ``get_config_section``,
  ``get_core_settings``): only ``cli`` does, as the composition root
  (flagged as ``<file>::settings::<layer> -> <name>``).

Imports between capabilities are covered by
``tests/unit/test_capabilities/test_capability_imports.py``. Existing
violations live in ``baselines/layering/<owner>.txt``.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CAPABILITIES_SRC = REPO_ROOT / "src" / "untaped" / "capabilities"
FORBIDDEN = {
    "domain": ("application", "infrastructure", "cli"),
    "application": ("infrastructure", "cli"),
    "infrastructure": ("cli", "application"),
}
# Only ``cli/`` resolves settings; lower layers receive narrowed models.
SETTINGS_READERS = frozenset({"app_context", "get_config_section", "get_core_settings"})


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _runtime_imports(tree: ast.Module) -> Iterator[ast.Import | ast.ImportFrom]:
    """Imports outside ``if TYPE_CHECKING:`` bodies (the ``else`` runs at runtime)."""
    exempt: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            for stmt in node.body:
                exempt.update(id(child) for child in ast.walk(stmt))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom) and id(node) not in exempt:
            yield node


def _package(path: Path) -> str:
    """The package a module's relative imports resolve against."""
    rel = path.relative_to(CAPABILITIES_SRC).with_suffix("")
    return ".".join(["untaped", "capabilities", *rel.parts[:-1]])


def _targets(node: ast.Import | ast.ImportFrom, package: str) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level == 0:
        return [node.module or ""]
    base = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
    return [f"{base}.{node.module}" if node.module else base]


def _violations(capability: str) -> Iterator[str]:
    root = CAPABILITIES_SRC / capability
    prefix = f"untaped.capabilities.{capability}"
    for layer, forbidden in FORBIDDEN.items():
        layer_dir = root / layer
        for path in sorted(layer_dir.rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            package = _package(path)
            for node in _runtime_imports(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "untaped.capability_api":
                    for alias in node.names:
                        if alias.name in SETTINGS_READERS:
                            yield f"{rel}::settings::{layer} -> {alias.name}"
                for target in _targets(node, package):
                    for other in forbidden:
                        banned = f"{prefix}.{other}"
                        if target == banned or target.startswith(f"{banned}."):
                            yield f"{rel}::layer::{layer} -> {target}"


def collect_violations() -> dict[str, list[str]]:
    found: dict[str, list[str]] = defaultdict(list)
    for child in sorted(CAPABILITIES_SRC.iterdir()):
        if (child / "__init__.py").is_file():
            found[child.name].extend(_violations(child.name))
    return found


def test_capability_layers_import_one_way(baseline: Any) -> None:
    baseline("layering", collect_violations())


def test_runtime_imports_skip_only_type_checking_bodies() -> None:
    source = """
from typing import TYPE_CHECKING
import untaped.capabilities.demo.cli.app
if TYPE_CHECKING:
    from untaped.capabilities.demo.application.ports import Port
else:
    from untaped.capabilities.demo.application import ports
from ..application import use_case
"""
    tree = ast.parse(source)
    package = "untaped.capabilities.demo.infrastructure"
    targets = [t for node in _runtime_imports(tree) for t in _targets(node, package)]
    assert "untaped.capabilities.demo.application.ports" not in targets
    assert "untaped.capabilities.demo.application" in targets  # the else branch
    assert "untaped.capabilities.demo.cli.app" in targets
    assert targets.count("untaped.capabilities.demo.application") == 2  # relative import
