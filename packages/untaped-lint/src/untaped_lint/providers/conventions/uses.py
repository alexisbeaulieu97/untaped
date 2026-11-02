"""``uses-deprecated`` in a plugin's source: the deprecated names its code imports.

untaped reads no plugin's code while it runs; the author's lint does, at the
author's request. Each name the plugin imports from untaped or another
plugin (:func:`~untaped_lint.infrastructure.imported_names.imported_names`:
``from M import N``, and ``M.N`` after ``import M``) is asked of the SDK
(:func:`untaped.sdk.testing.deprecation_of`), old names included, which type
checkers can't see. Wherever the import sits (in a function, under a
``try``) it counts. Violations are
``<file>:<line>::uses-deprecated::<sentence>``, a warning;
``# untaped: allow uses-deprecated`` on the line waives one. A star import
is ``import-boundary``'s, and a deprecated contract method a provider fills
comes from core's check of the composed plugin.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from untaped.sdk import InstalledPlugin
from untaped.sdk.testing import deprecation_of
from untaped_lint.domain.allow import allows
from untaped_lint.infrastructure.files import SourceFile
from untaped_lint.infrastructure.imported_names import imported_names

RULE = "uses-deprecated"


def uses_violations(
    plugin: InstalledPlugin,
    source_dir: Path,
    files: Sequence[SourceFile],
    *,
    plugins: Sequence[InstalledPlugin],
) -> list[str]:
    """Each import in ``files`` of a deprecated name of untaped or another plugin.

    ``plugins`` are every installed plugin's records: their packages are the
    plugins an import may reach. Paths are relative to ``source_dir``'s
    parent. A plugin whose record names no package has nothing to check.
    """
    package = plugin.package
    if package is None:
        return []
    roots = ("untaped", *(each.package for each in plugins if each.package not in (None, package)))
    found: list[str] = []
    for source in files:
        parts = source.path.relative_to(source_dir).with_suffix("").parts
        is_package = parts[-1] == "__init__"
        module = ".".join([package, *(parts[:-1] if is_package else parts)])
        rel = source.path.relative_to(source_dir.parent).as_posix()
        for each in imported_names(source.tree, module, own=package, is_package=is_package):
            if each.star or each.name is None or not _within(each.module, roots):
                continue
            sentence = _deprecation(each.module, each.name)
            if sentence is not None and not allows(source.lines, each.line, RULE):
                found.append(f"{rel}:{each.line}::{RULE}::{sentence}")
    return found


def _within(module: str, roots: Sequence[str | None]) -> bool:
    return any(root and (module == root or module.startswith(f"{root}.")) for root in roots)


def _deprecation(module: str, name: str) -> str | None:
    """``module.name``'s deprecation sentence; ``None`` for a name that isn't, or isn't there.

    A missing name or a module failing to import is not this rule's
    business: the plugin's own tests meet it.
    """
    try:
        return deprecation_of(module, name)
    except Exception:
        return None
