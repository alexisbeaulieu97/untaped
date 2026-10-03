"""Convention checks for capabilities and the root shell.

See ``docs/reference/conventions.md#enforcement``.

Each check reads one installed package: its command subtree from the real
composition and its own source files, wherever they are installed, so a
third-party provider is checked exactly like a first-party one. Provider tests call
:func:`untaped.testing.check_conventions`; this package is internal.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from importlib.util import find_spec
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from untaped.bootstrap import build_root_app, composition
from untaped.capabilities.registry import (
    CapabilitySpec,
    ProviderCandidate,
    discover_candidates,
)
from untaped.conventions.help_tree import ROOT_COMMANDS, help_tree_violations
from untaped.conventions.imports import import_boundary_violations
from untaped.conventions.layering import layering_violations
from untaped.conventions.messages import message_violations
from untaped.conventions.source import source_files
from untaped.conventions.structure import structure_violations


def capability_violations(
    name: str,
    *,
    tests_dir: Path | None = None,
    candidates: Sequence[ProviderCandidate] | None = None,
) -> list[str]:
    """Every convention violation of the installed capability ``name``.

    Builds the root once (from discovered candidates), finds the
    registered capability, and runs help_tree, messages, structure, layering
    and import-boundary over its command subtree and package. The private-test-import
    check runs only when ``tests_dir`` is given. ``candidates`` replaces
    entry-point discovery (as in :func:`untaped.bootstrap.compose_root`), so
    a test can check a provider that is not installed. Lines are
    ``<where>::<rule>::<detail>``, sorted.
    """
    candidates = list(discover_candidates()) if candidates is None else candidates
    root = build_root_app(candidates=candidates)
    spec = next(
        (
            capability.spec
            for capability in composition().capabilities
            if capability.spec.name == name
        ),
        None,
    )
    if spec is None:
        raise LookupError(f"no installed capability named {name!r}")
    package, source_dir = _package_of(spec)
    files = list(source_files(source_dir))
    capability_packages, declared = _boundary(name, candidates)
    return sorted(
        [
            *help_tree_violations(root, [name]),
            *message_violations(source_dir, files),
            *structure_violations(spec, package, source_dir, files, tests_dir=tests_dir),
            *layering_violations(package, source_dir, files),
            *import_boundary_violations(
                package,
                source_dir,
                files,
                capability_packages=capability_packages,
                declared=declared,
            ),
        ]
    )


def _required(requirements: Sequence[str]) -> set[str]:
    """Canonical names of the ``Requires-Dist`` strings a default install pulls in.

    A requirement guarded by an ``extra`` marker is left out; other markers
    (``python_version``, ``sys_platform``…) are ignored; invalid strings are skipped.
    """
    names: set[str] = set()
    for requirement in requirements:
        try:
            parsed = Requirement(requirement)
        except InvalidRequirement:
            continue
        if parsed.marker is None or not _mentions_extra(str(parsed.marker)):
            names.add(canonicalize_name(parsed.name))
    return names


def _mentions_extra(marker: str) -> bool:
    """Whether ``marker`` tests the ``extra`` variable (quoted values aside)."""
    return re.search(r"\bextra\b", re.sub(r"\"[^\"]*\"|'[^']*'", "", marker)) is not None


def _boundary(
    name: str, candidates: Sequence[ProviderCandidate]
) -> tuple[dict[str, str], frozenset[str]]:
    """Capability packages (to distributions) and what capability ``name`` may import from.

    Every candidate counts as a capability, composed or quarantined: a
    ``module:attr`` target names its package; a callable target (as from
    :func:`untaped.testing.provider_candidate`) gives it through the spec it
    provides, and is skipped when that cannot be resolved. The checked
    capability's own distribution is always declared, so capabilities sharing
    a distribution may import each other's ``api``.
    """
    found = list(candidates)
    packages: dict[str, str] = {}
    for candidate in found:
        package = _candidate_package(candidate.target)
        if package is not None:
            packages[package] = canonicalize_name(candidate.distribution)
    own = next((candidate for candidate in found if candidate.name == name), None)
    if own is None:
        return packages, frozenset()
    declared = {str(canonicalize_name(own.distribution)), *_required(own.requires_dist)}
    return packages, frozenset(declared)


def _candidate_package(target: object) -> str | None:
    """The capability package of a candidate ``target``, or ``None`` when unresolvable."""
    if isinstance(target, str):
        return target.partition(":")[0] if ":" in target else None
    if not callable(target):
        return None
    try:
        spec = target()
        return _package_of(spec)[0] if isinstance(spec, CapabilitySpec) else None
    except Exception:
        return None


def core_violations() -> list[str]:
    """Violations in the root commands and ``untaped.management`` (repo-internal)."""
    root = build_root_app(candidates=[])
    management = _source_dir("untaped.management")
    return sorted(
        [
            *help_tree_violations(root, sorted(ROOT_COMMANDS)),
            *message_violations(management, list(source_files(management))),
        ]
    )


def _package_of(spec: CapabilitySpec) -> tuple[str, Path]:
    """The package owning ``spec`` and its source directory.

    That is the app factory's module when it is a package, else the
    module's parent package.
    """
    module = getattr(spec.app_factory, "__module__", None) or ""
    found = find_spec(module) if module else None
    if found is not None and found.submodule_search_locations:
        return module, Path(found.submodule_search_locations[0])
    package = module.rpartition(".")[0]
    if not package:
        raise LookupError(f"capability {spec.name!r}: its app factory is not defined in a package")
    return package, _source_dir(package)


def _source_dir(package: str) -> Path:
    found = find_spec(package)
    if found is None or not found.submodule_search_locations:
        raise LookupError(f"package {package!r} has no source directory")
    return Path(found.submodule_search_locations[0])
