"""Import boundary of a plugin: core only through the SDK, other plugins only through ``api``.

A plugin's own source may import ``untaped`` only as one of the SDK modules
(``untaped.sdk`` and its groups, :data:`SDK_MODULES`), as
``untaped.core.api`` (the core commands a hint names) or as the experimental
screens kit (``untaped.screen.core`` and ``untaped.screen.components``'s
modules, :func:`is_screen_kit`: outside the SDK promise, a kit a plugin takes
knowing it is one), and another plugin only as that
plugin's ``api`` module, and only when its distribution requires the
other's (by default: the ``requires`` of its ``untaped.installed_plugin``
record). Which packages are plugins
comes from every installed plugin's record, quarantined ones included. Its
own package is always allowed; third-party libraries are not this rule's
business. Every import counts, including function-level and
``TYPE_CHECKING`` ones. A star import of untaped or another plugin is
flagged too: lint checks each name a plugin imports (``uses-deprecated``),
and can't tell what a star binds. Violations are
``<file>:<line>::import-boundary::<detail>``;
``# untaped: allow import-boundary`` on the import line waives one.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

from untaped.sdk import InstalledPlugin
from untaped_lint.domain.allow import allows
from untaped_lint.infrastructure.files import SourceFile, import_targets

RULE = "import-boundary"
_SDK = "untaped.sdk"
#: The modules of ``untaped`` a plugin may import: those the surface promises
#: (core's ``SDK_MODULES``; lint's tests hold the two equal): the root and its groups.
SDK_MODULES = frozenset(
    {
        "untaped.sdk",
        "untaped.sdk.contracts",
        "untaped.sdk.health",
        "untaped.sdk.network",
        "untaped.sdk.process",
        "untaped.sdk.projects",
        "untaped.sdk.prompt",
        "untaped.sdk.storage",
        "untaped.sdk.testing",
    }
)

#: Core's own ``api`` module: the commands a plugin names in a hint (``Set.spelled(...)``).
CORE_API = "untaped.core.api"
#: The screens kit's modules (``docs/screens.md``): its core and its components.
SCREEN_CORE = "untaped.screen.core"
SCREEN_COMPONENTS = "untaped.screen.components"


def is_screen_kit(target: str) -> bool:
    """Whether ``target`` is the screens kit: ``untaped.screen.core``, or
    ``untaped.screen.components`` and its modules, with no private (``_``) part."""
    if target in (SCREEN_CORE, SCREEN_COMPONENTS):
        return True
    rest = target.removeprefix(f"{SCREEN_COMPONENTS}.")
    return rest != target and not any(part.startswith("_") for part in rest.split("."))


def _targets(
    node: ast.Import | ast.ImportFrom, module_package: str, roots: frozenset[str]
) -> Iterator[str]:
    """The modules ``node`` imports; ``from <root> import x`` binds ``<root>.x``."""
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in roots:
        for alias in node.names:
            yield f"{node.module}.{alias.name}"
        return
    yield from import_targets(node, module_package)


def _canonical(distribution: str) -> str:
    """``distribution`` normalised as ``requires`` spells names (PEP 503)."""
    return re.sub(r"[-_.]+", "-", distribution).lower()


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
    if (
        _is_within(target, "untaped")
        and target not in SDK_MODULES
        and target != CORE_API
        and not is_screen_kit(target)
    ):
        return f"imports {target}; use {_SDK}"
    return None


def _star(node: ast.Import | ast.ImportFrom, package: str, roots: frozenset[str]) -> str | None:
    """What a star import of untaped or another plugin breaks; ``None`` for any other import."""
    if not isinstance(node, ast.ImportFrom) or node.level or node.module is None:
        return None
    if not any(alias.name == "*" for alias in node.names) or _is_within(node.module, package):
        return None
    if not any(_is_within(node.module, root) for root in roots):
        return None
    return f"imports * from {node.module}; import each name, so lint can check it"


def import_boundary_violations(
    plugin: InstalledPlugin,
    source_dir: Path,
    files: Sequence[SourceFile],
    *,
    plugins: Sequence[InstalledPlugin],
) -> list[str]:
    """Violations in ``files`` of ``plugin`` (its code in ``source_dir``).

    ``plugins`` are every installed plugin's records: their packages are the
    plugins another import may reach. Paths are relative to ``source_dir``'s
    parent. A plugin whose record names no package has nothing to check. The
    root plugin, untaped itself, is the host: all of ``untaped`` is its own,
    and only other plugins are beyond its boundary.
    """
    package = plugin.package
    if package is None:
        return []
    own = "untaped" if plugin.root else package
    plugin_packages = {
        each.package: _canonical(each.distribution) for each in plugins if each.package is not None
    }
    declared = frozenset({_canonical(plugin.distribution), *plugin.requires})
    roots = frozenset({"untaped", *plugin_packages})
    found: list[str] = []
    for source in files:
        parts = source.path.relative_to(source_dir).with_suffix("").parts
        module_package = ".".join([package, *parts[:-1]])
        rel = source.path.relative_to(source_dir.parent).as_posix()
        for node in ast.walk(source.tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            star = _star(node, own, roots)
            if star is not None:
                if not allows(source.lines, node.lineno, RULE):
                    found.append(f"{rel}:{node.lineno}::{RULE}::{star}")
                continue
            for target in _targets(node, module_package, roots):
                detail = _detail(target, own, plugin_packages, declared)
                if detail is not None and not allows(source.lines, node.lineno, RULE):
                    found.append(f"{rel}:{node.lineno}::{RULE}::{detail}")
    return found
