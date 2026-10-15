"""Import-boundary lint: layers inside one plugin point one way.

``cli → application → domain`` and ``infrastructure → domain``
(``docs/reference/conventions.md``). An import that crosses a layer the wrong
way, ``TYPE_CHECKING`` blocks included, is flagged as
``<file>::layer::<from layer> -> <imported module>``:

- ``domain`` imports nothing from ``application``, ``infrastructure`` or ``cli``;
- ``application`` imports nothing from ``infrastructure`` or ``cli``;
- ``infrastructure`` imports nothing from ``cli`` and ``application`` only
  under ``TYPE_CHECKING`` (adapters satisfy ports structurally);
- none of them resolves settings (``app_context``, ``get_config_section``): only ``cli``
  does, as the composition root (flagged as ``<file>::settings::<layer> -> <name>``).

``# untaped: allow layer`` (or ``settings``) on the import line suppresses one.
Imports between plugins are checked by ``untaped.conventions.imports``.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Sequence
from pathlib import Path

from untaped.testing import allows, import_targets
from untaped_lint.infrastructure.files import SourceFile

FORBIDDEN = {
    "domain": ("application", "infrastructure", "cli"),
    "application": ("infrastructure", "cli"),
    "infrastructure": ("cli", "application"),
}
# Imports a ``TYPE_CHECKING`` block may make against the layers: an adapter
# names the port it satisfies.
TYPING_ONLY = frozenset({("infrastructure", "application")})
# Only ``cli/`` resolves settings; lower layers receive narrowed models.
SETTINGS_READERS = frozenset({"app_context", "get_config_section"})


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _typing_only(tree: ast.Module) -> set[int]:
    """The ids of every node inside an ``if TYPE_CHECKING:`` body (the ``else`` runs)."""
    inside: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            for stmt in node.body:
                inside.update(id(child) for child in ast.walk(stmt))
    return inside


def _import_violations(
    node: ast.Import | ast.ImportFrom,
    layer: str,
    package: str,
    module_package: str,
    *,
    typing_only: bool,
) -> Iterator[tuple[str, str]]:
    """Yield ``(rule, detail)`` for one import in ``layer`` of ``package``."""
    if not typing_only and isinstance(node, ast.ImportFrom) and node.module == "untaped.sdk":
        for alias in node.names:
            if alias.name in SETTINGS_READERS:
                yield "settings", f"{layer} -> {alias.name}"
    for target in import_targets(node, module_package):
        for other in FORBIDDEN[layer]:
            if typing_only and (layer, other) in TYPING_ONLY:
                continue
            banned = f"{package}.{other}"
            if target == banned or target.startswith(f"{banned}."):
                yield "layer", f"{layer} -> {target}"


def layering_violations(package: str, source_dir: Path, files: Sequence[SourceFile]) -> list[str]:
    """Violations in ``files`` of ``package`` (code in ``source_dir``).

    Paths are relative to ``source_dir``'s parent.
    """
    found: list[str] = []
    for source in files:
        parts = source.path.relative_to(source_dir).with_suffix("").parts
        layer = parts[0] if len(parts) > 1 else ""
        if layer not in FORBIDDEN:
            continue
        rel = source.path.relative_to(source_dir.parent).as_posix()
        module_package = ".".join([package, *parts[:-1]])
        typing_only = _typing_only(source.tree)
        for node in ast.walk(source.tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            for rule, detail in _import_violations(
                node, layer, package, module_package, typing_only=id(node) in typing_only
            ):
                if not allows(source.lines, node.lineno, rule):
                    found.append(f"{rel}::{rule}::{detail}")
    return found
