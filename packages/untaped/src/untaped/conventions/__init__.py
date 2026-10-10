"""Convention checks for plugins and the root app.

See ``docs/reference/conventions.md#enforcement``.

Each check reads one installed package: its command subtree from the real
composition and its own source files, wherever they are installed, so a
third-party plugin is checked exactly like a first-party one. Plugin tests call
:func:`untaped.testing.check_conventions`; this package is internal.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from importlib.util import find_spec
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from untaped.bootstrap import SHELL_SPEC, build_root_app, composition
from untaped.conventions.blob_reader import blob_reader_violations
from untaped.conventions.help_tree import help_tree_violations
from untaped.conventions.imports import import_boundary_violations
from untaped.conventions.layering import layering_violations
from untaped.conventions.messages import message_violations
from untaped.conventions.settings_names import settings_name_violations
from untaped.conventions.source import source_files
from untaped.conventions.stability import stability_violations
from untaped.conventions.structure import structure_violations
from untaped.conventions.terminal_boundary import terminal_boundary_violations
from untaped.plugins.registry import (
    ROOT_MANAGEMENT_COMMANDS,
    PluginCandidate,
    PluginSpec,
    discover_candidates,
    owner_requirement,
)
from untaped.settings import Settings, model_sections


def plugin_violations(
    name: str,
    *,
    tests_dir: Path | None = None,
    candidates: Sequence[PluginCandidate] | None = None,
) -> list[str]:
    """Every convention violation of the installed plugin ``name``.

    Builds the root once (from discovered candidates), finds the
    registered plugin, and runs help_tree, stability, messages, structure,
    layering, terminal-boundary, blob-reader and import-boundary over its
    command subtree and package. The private-test-import check runs only when ``tests_dir`` is
    given. ``candidates`` replaces entry-point discovery (as in
    :func:`untaped.bootstrap.compose_root`), so a test can check a provider
    that is not installed. Lines are ``<where>::<rule>::<detail>``, sorted. A
    quarantined plugin's one violation is the reason composition refused it.
    """
    candidates = list(discover_candidates()) if candidates is None else candidates
    root = build_root_app(candidates=candidates)
    spec = next(
        (plugin.spec for plugin in composition().plugins if plugin.spec.name == name),
        None,
    )
    if spec is None:
        record = next((r for r in composition().quarantine if r.name == name), None)
        if record is None:
            raise LookupError(f"no installed plugin named {name!r}")
        # A quarantined plugin has no subtree to check; why it was refused
        # (a broken settings-key declaration, say) is its one violation.
        return [f"{name}::quarantined::{record.reason}: {record.detail}"]
    own = next((candidate for candidate in candidates if candidate.name == name), None)
    package, source_dir = package_of(spec, None if own is None else own.target)
    files = list(source_files(source_dir))
    plugin_packages, declared = _boundary(name, candidates, frozenset(spec.provides))
    commands = [] if spec.app_factory is None else [name]  # its mounted subtree
    return sorted(
        [
            *_name_violations(name, package, own),
            *_provides_violations(spec, own, candidates),
            *help_tree_violations(root, commands),
            *stability_violations(root, composition(), commands, spec=spec),
            *message_violations(source_dir, files),
            *structure_violations(spec, package, source_dir, files, tests_dir=tests_dir),
            *layering_violations(package, source_dir, files),
            *terminal_boundary_violations(source_dir, files),
            *blob_reader_violations(package, source_dir, files),
            *import_boundary_violations(
                package,
                source_dir,
                files,
                plugin_packages=plugin_packages,
                declared=declared,
            ),
        ]
    )


def _name_violations(name: str, package: str, candidate: PluginCandidate | None) -> list[str]:
    """Where the plugin's distribution or import package is not named after it.

    One name runs through a plugin: ``untaped-<name>`` on PyPI,
    ``untaped_<name>`` to import, ``<name>`` as entry point, config section
    and command group. Composition already holds the entry point to the
    spec's name.
    """
    violations: list[str] = []
    expected_package = "untaped_" + name.replace("-", "_")
    top = package.partition(".")[0]
    if top != expected_package:
        violations.append(
            f"{name}::plugin-name::import package {top!r} is not {expected_package!r}"
        )
    expected_distribution = f"untaped-{name}"
    if candidate is not None and canonicalize_name(candidate.distribution) != expected_distribution:
        violations.append(
            f"{name}::plugin-name::distribution {candidate.distribution!r} "
            f"is not {expected_distribution!r}"
        )
    return violations


def _required(requirements: Sequence[str], extras: frozenset[str] = frozenset()) -> set[str]:
    """Canonical names of the ``Requires-Dist`` strings the plugin may import from.

    That is what a default install pulls in, plus what an extra in
    ``extras`` (the plugin's ``provides`` keys) adds: the owner a provider
    declares under ``untaped-acme[<owner>]``. Other extra-guarded
    requirements are left out; other markers (``python_version``,
    ``sys_platform``…) are ignored; invalid strings are skipped.
    """
    names: set[str] = set()
    for requirement in requirements:
        try:
            parsed = Requirement(requirement)
        except InvalidRequirement:
            continue
        if (
            parsed.marker is None
            or not _mentions_extra(str(parsed.marker))
            or _added_by(parsed, extras)
        ):
            names.add(canonicalize_name(parsed.name))
    return names


def _added_by(requirement: Requirement, extras: frozenset[str]) -> bool:
    """Whether one of ``extras`` turns ``requirement`` on."""
    marker = requirement.marker
    if marker is None:
        return False
    return any(marker.evaluate({"extra": extra}) for extra in extras)


def _mentions_extra(marker: str) -> bool:
    """Whether ``marker`` tests the ``extra`` variable (quoted values aside)."""
    return re.search(r"\bextra\b", re.sub(r"\"[^\"]*\"|'[^']*'", "", marker)) is not None


def _provides_violations(
    spec: PluginSpec, candidate: PluginCandidate | None, candidates: Sequence[PluginCandidate]
) -> list[str]:
    """Where a provider doesn't declare the ranges its offers rely on.

    A plugin with ``provides`` requires ``untaped`` with a version range, and
    each owner it fills contracts for under an extra named after the owner,
    with a range too (``untaped-<owner>>=M,<M+1``): the registry checks the
    installed owner against that range (``owner-out-of-range``).
    """
    if not spec.provides:
        return []
    requires = () if candidate is None else candidate.requires_dist
    rule = f"{spec.name}::provides-requirement::"
    found: list[str] = []
    core = [
        parsed
        for parsed in (_parse(line) for line in requires)
        if parsed is not None
        and canonicalize_name(parsed.name) == "untaped"
        and not _mentions_extra(str(parsed.marker or ""))
    ]
    if not any(parsed.specifier or parsed.url for parsed in core):
        found.append(f"{rule}requires no untaped version range (untaped>=M,<M+1)")
    distributions = {c.name: c.distribution for c in candidates}
    for owner in sorted(spec.provides):
        distribution = str(canonicalize_name(distributions.get(owner, f"untaped-{owner}")))
        requirement = owner_requirement(requires, owner, distribution)
        if requirement is None:
            found.append(
                f"{rule}provides for {owner} but has no {owner!r} extra requiring {distribution}"
            )
        elif not (requirement.specifier or requirement.url):
            found.append(f"{rule}its {owner!r} extra requires {distribution} with no version range")
    return found


def _parse(line: str) -> Requirement | None:
    try:
        return Requirement(line)
    except InvalidRequirement:
        return None


def _boundary(
    name: str, candidates: Sequence[PluginCandidate], extras: frozenset[str] = frozenset()
) -> tuple[dict[str, str], frozenset[str]]:
    """Plugin packages (to distributions) and what plugin ``name`` may import from.

    Every candidate counts as a plugin, composed or quarantined: a
    ``module:attr`` target names its package; a spec target (as from
    :func:`untaped.testing.plugin_candidate`) gives it through what the spec
    declares, and is skipped when that cannot be resolved. The checked
    plugin's own distribution is always declared, as is what one of
    ``extras`` (its ``provides`` keys) requires.
    """
    found = list(candidates)
    packages: dict[str, str] = {}
    for candidate in found:
        package = candidate_package(candidate.target)
        if package is not None:
            packages[package] = canonicalize_name(candidate.distribution)
    own = next((candidate for candidate in found if candidate.name == name), None)
    if own is None:
        return packages, frozenset()
    declared = {str(canonicalize_name(own.distribution)), *_required(own.requires_dist, extras)}
    return packages, frozenset(declared)


def candidate_package(target: object) -> str | None:
    """The plugin package of a candidate ``target``, or ``None`` when unresolvable."""
    if isinstance(target, str):
        return target.partition(":")[0] if ":" in target else None
    if not isinstance(target, PluginSpec):
        return None
    try:
        return package_of(target)[0]
    except LookupError:
        return None


def core_violations() -> list[str]:
    """Violations in the root commands, ``untaped.management`` and core's sections.

    Repo-internal.
    """
    root = build_root_app(candidates=[])
    result = composition()
    management = _source_dir("untaped.management")
    sections = {**model_sections(Settings), SHELL_SPEC.section: SHELL_SPEC.settings}
    src = _source_dir("untaped").parent
    return sorted(
        [
            *help_tree_violations(root, sorted(ROOT_MANAGEMENT_COMMANDS)),
            *stability_violations(root, result, ROOT_MANAGEMENT_COMMANDS, sections=sections),
            *message_violations(management, list(source_files(management))),
            *(
                line
                for section, model in sections.items()
                for line in settings_name_violations(section, model, src)
            ),
        ]
    )


def package_of(spec: PluginSpec, target: object = None) -> tuple[str, Path]:
    """The package owning ``spec`` and its source directory.

    That is the module of what the spec declares (its app factory, else its
    settings or state model), or of its ``package:SPEC`` entry-point
    ``target`` when it declares none of them, when it is a package, else the
    module's parent package.
    """
    declared = spec.app_factory or spec.settings or spec.state
    if declared is not None:
        module = getattr(declared, "__module__", None) or ""
    elif isinstance(target, str) and ":" in target:
        module = target.partition(":")[0]
    else:
        raise LookupError(
            f"plugin {spec.name!r} declares nothing to locate its package by; "
            "pass a PluginCandidate whose target is 'package:SPEC'"
        )
    found = find_spec(module) if module else None
    if found is not None and found.submodule_search_locations:
        return module, Path(found.submodule_search_locations[0])
    package = module.rpartition(".")[0]
    if not package:
        raise LookupError(
            f"plugin {spec.name!r}: its app factory or settings are not defined in a package"
        )
    return package, _source_dir(package)


def _source_dir(package: str) -> Path:
    found = find_spec(package)
    if found is None or not found.submodule_search_locations:
        raise LookupError(f"package {package!r} has no source directory")
    return Path(found.submodule_search_locations[0])
