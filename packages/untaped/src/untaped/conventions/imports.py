"""Import boundary of a plugin: core only through ``untaped.sdk``.

A plugin's own source may import ``untaped`` only as ``untaped.sdk``,
and another plugin only as that plugin's ``api`` module, and only
when its distribution declares a dependency on the other's. Its own package
is always allowed; third-party libraries are not this rule's business.
Every import counts, including function-level and ``TYPE_CHECKING`` ones.
Violations are ``<file>:<line>::import-boundary::<detail>``;
``# untaped: allow import-boundary`` on the import line waives one.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

from untaped.conventions.allow import allowed
from untaped.conventions.source import SourceFile, import_targets

RULE = "import-boundary"
_SDK = "untaped.sdk"


def _targets(
    node: ast.Import | ast.ImportFrom, module_package: str, roots: frozenset[str]
) -> Iterator[str]:
    """The modules ``node`` imports; ``from <root> import x`` binds ``<root>.x``."""
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in roots:
        for alias in node.names:
            yield f"{node.module}.{alias.name}"
        return
    yield from import_targets(node, module_package)


def _is_within(target: str, package: str) -> bool:
    return target == package or target.startswith(f"{package}.")


def _detail(
    target: str,
    package: str,
    plugin_packages: Mapping[str, str],
    declared: frozenset[str],
) -> str | None:
    if _is_within(target, package):
        return None
    other = max(
        (name for name in plugin_packages if _is_within(target, name)),
        key=len,
        default=None,
    )
    if other is not None:
        if target != f"{other}.api":
            return f"imports {target}; use {other}.api"
        if plugin_packages[other] not in declared:
            return (
                f"imports {target} but its distribution does not depend on "
                f"{plugin_packages[other]}'s"
            )
        return None
    if _is_within(target, "untaped") and target != _SDK:
        return f"imports {target}; use {_SDK}"
    return None


def import_boundary_violations(
    package: str,
    source_dir: Path,
    files: Sequence[SourceFile],
    *,
    plugin_packages: Mapping[str, str],
    declared: frozenset[str],
) -> list[str]:
    """Violations in ``files`` of ``package`` (code in ``source_dir``).

    ``plugin_packages`` maps each plugin's package to its canonical
    distribution name; ``declared`` holds the canonical distribution names
    the checked plugin may import from (its own and its requirements).
    Paths are relative to ``source_dir``'s parent.
    """
    roots = frozenset({"untaped", *plugin_packages})
    found: list[str] = []
    for source in files:
        parts = source.path.relative_to(source_dir).with_suffix("").parts
        module_package = ".".join([package, *parts[:-1]])
        rel = source.path.relative_to(source_dir.parent).as_posix()
        for node in ast.walk(source.tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            for target in _targets(node, module_package, roots):
                detail = _detail(target, package, plugin_packages, declared)
                if detail is not None and not allowed(source.lines, node.lineno, RULE):
                    found.append(f"{rel}:{node.lineno}::{RULE}::{detail}")
    return found
