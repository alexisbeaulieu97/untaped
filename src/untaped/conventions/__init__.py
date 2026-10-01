"""Convention checks for capabilities and the root shell (``docs/conventions.md``).

Each check reads one installed package: its command subtree from the real
composition and its own source files, wherever they are installed, so a
third-party provider is checked exactly like a built-in. Provider tests call
:func:`untaped.testing.check_conventions`; this package is internal.
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from importlib.util import find_spec
from pathlib import Path

from untaped.bootstrap import build_root_app, compose_root
from untaped.capabilities.registry import CapabilitySpec, ExternalProvider
from untaped.conventions.help_tree import ROOT_COMMANDS, help_tree_violations
from untaped.conventions.layering import layering_violations
from untaped.conventions.messages import message_violations
from untaped.conventions.structure import structure_violations


def capability_violations(
    name: str,
    *,
    tests_dir: Path | None = None,
    externals: Sequence[ExternalProvider] | None = None,
) -> list[str]:
    """Every convention violation of the installed capability ``name``.

    Composes the root (built-ins and discovered externals), finds the
    registered capability, and runs help_tree, messages, structure and
    layering over its command subtree and package. The private-test-import
    check runs only when ``tests_dir`` is given. ``externals`` replaces
    entry-point discovery (as in :func:`untaped.bootstrap.compose_root`), so
    a test can check a provider that is not installed. Lines are
    ``<where>::<rule>::<detail>``, sorted.
    """
    result = compose_root(externals=externals)
    spec = next(
        (capability.spec for capability in result.capabilities if capability.spec.name == name),
        None,
    )
    if spec is None:
        raise LookupError(f"no installed capability named {name!r}")
    package = _package_of(spec)
    source_dir = _source_dir(package)
    root = build_root_app(externals=externals)
    return sorted(
        [
            *help_tree_violations(root, [name]),
            *message_violations(source_dir),
            *structure_violations(spec, package, source_dir, tests_dir=tests_dir),
            *layering_violations(package, source_dir),
        ]
    )


def core_violations() -> list[str]:
    """Violations in the root commands and ``untaped.management`` (repo-internal)."""
    root = build_root_app(externals=[])
    return sorted(
        [
            *help_tree_violations(root, sorted(ROOT_COMMANDS)),
            *message_violations(_source_dir("untaped.management")),
        ]
    )


def _package_of(spec: CapabilitySpec) -> str:
    """The package owning ``spec``: its app factory's module, or that module's package."""
    module = spec.app_factory.__module__
    if hasattr(importlib.import_module(module), "__path__"):
        return module
    return module.rpartition(".")[0]


def _source_dir(package: str) -> Path:
    found = find_spec(package)
    if found is None or not found.submodule_search_locations:
        raise LookupError(f"package {package!r} has no source directory")
    return Path(found.submodule_search_locations[0])
