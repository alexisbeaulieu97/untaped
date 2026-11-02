"""The rules that read a composed plugin: what the ``conventions`` check row runs.

Help tree, guides and overview lines, stability marks, settings names and
structure's class checks, each family in its module beside this one. See
``docs/reference/conventions.md#enforcement``.

Each check reads one installed package: its command subtree from the real
composition and its own source files, wherever they are installed, so a
third-party plugin is checked exactly like a first-party one. Plugin tests read
its rows through :func:`untaped.sdk.testing.check_plugin`; this module is internal.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import tomllib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from cyclopts import App
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from untaped.core.plugin_check.check import fill_owners
from untaped.core.plugin_check.guides import guide_violations
from untaped.core.plugin_check.help_tree import help_tree_violations
from untaped.core.plugin_check.stability import (
    deprecation_text_violations,
    stability_violations,
    undated_violations,
)
from untaped.core.plugin_check.structure import structure_class_violations, unimportable
from untaped.runtime.bootstrap import build_root_app, composition
from untaped.runtime.plugin_package import package_of
from untaped.runtime.registry import PluginCandidate, discover_candidates
from untaped.runtime.requirements import (
    floor_for,
    floor_of,
    is_exact,
    is_upper,
    parse_requirement,
    plain_requirements,
)
from untaped.sdk.storage.installed_code import _editable_root

if TYPE_CHECKING:
    from untaped.runtime.composed import ComposedPlugin


def plugin_violations(
    name: str, *, candidates: Sequence[PluginCandidate] | None = None
) -> list[str]:
    """Every convention violation only the composed root can tell: what ``check_plugin`` reports.

    Plugin and distribution names, requirement floors, the help tree,
    guides and overview lines, stability marks and structure's class checks
    (exceptions, ports, frozen settings, setting names). The rules that read
    the plugin's files, the import boundary among them, are lint's and are
    not run here. ``candidates`` replaces entry-point discovery (as in
    :func:`untaped.runtime.bootstrap.compose_root`), so a test can check a
    provider that is not installed. Lines are ``<where>::<rule>::<detail>``,
    sorted. A quarantined plugin's one violation is the reason composition
    refused it.
    """
    found = _checked(name, candidates)
    if isinstance(found, list):
        return found
    return sorted(_core_lines(found))


@dataclass(frozen=True)
class _Checked:
    """One composed plugin, located: what each family of checks reads."""

    name: str
    root: App
    plugin: ComposedPlugin
    own: PluginCandidate
    candidates: Sequence[PluginCandidate]
    package: str
    source_dir: Path


def _checked(name: str, candidates: Sequence[PluginCandidate] | None) -> _Checked | list[str]:
    """The composed plugin ``name``, or a quarantined plugin's one violation."""
    candidates = list(discover_candidates()) if candidates is None else candidates
    root = build_root_app(candidates=candidates)
    checked = next(
        (
            plugin
            for plugin in composition().plugins
            # the root plugin is untaped itself, not one installed into it
            if plugin.name == name and not plugin.root
        ),
        None,
    )
    if checked is None:
        record = next((r for r in composition().quarantine if r.name == name), None)
        if record is None:
            raise LookupError(f"no installed plugin named {name!r}")
        # A quarantined plugin has no subtree to check; why it was refused
        # (a broken settings-key declaration, say) is its one violation.
        return [f"{name}::quarantined::{record.reason}: {record.detail}"]
    own = next((candidate for candidate in candidates if candidate.name == name), None)
    if own is None:  # a composed plugin is one of the candidates
        raise LookupError(f"no candidate named {name!r}")
    package, source_dir = package_of(checked)
    unreadable = unimportable(package, source_dir)
    if unreadable is not None:
        # The class checks can't import the plugin; the file is its one violation.
        return [unreadable]
    return _Checked(name, root, checked, own, candidates, package, source_dir)


def _core_lines(checked: _Checked) -> list[str]:
    plugin, name = checked.plugin, checked.name
    commands = [name] if plugin.has_commands else []  # its mounted subtree
    return [
        *_name_violations(name, checked.package, checked.own),
        *_floor_violations(plugin, checked.own, checked.candidates),
        *help_tree_violations(checked.root, commands),
        *guide_violations(checked.root, composition(), plugin),
        *stability_violations(checked.root, composition(), commands, plugin=plugin),
        *undated_violations(plugin, checked.own),
        *_sdk_deprecations(checked.package),
        *structure_class_violations(plugin, checked.package, checked.source_dir),
    ]


def _sdk_deprecations(package: str) -> list[str]:
    """``deprecation_text_violations`` of the plugin's ``api`` and ``testing`` modules."""
    from untaped.core.surface import plugin_sdk_modules  # noqa: PLC0415 - loaded only when checked

    return [
        line
        for path in plugin_sdk_modules(package)
        for line in deprecation_text_violations(importlib.import_module(path))
    ]


def _name_violations(name: str, package: str, candidate: PluginCandidate | None) -> list[str]:
    """Where the plugin's distribution or import package is not named after it.

    One name runs through a plugin: ``untaped-<name>`` on PyPI,
    ``untaped_<name>`` to import, ``<name>`` as entry point, config section
    and command group. Composition already holds the entry point to the
    plugin's name.
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


def _floor_violations(
    plugin: ComposedPlugin, candidate: PluginCandidate, candidates: Sequence[PluginCandidate]
) -> list[str]:
    """Where a plugin's requirements on untaped or another plugin are not floors.

    A plugin requires ``untaped``, and each owner whose contract a fill
    subclasses, since it imports them. Every requirement on ``untaped`` or an
    installed plugin is a floor (``>=``, with ``!=`` beside it if needed), never
    an upper limit or an exact pin: one stale plugin would otherwise hold back
    every install that has it. Packages released together keep their pins:
    an exact pin is fine when both are editable installs from one uv workspace.
    A plugin class composed without being installed has no requirements to read.
    """
    rule = f"{plugin.name}::requirement-floor::"
    own = canonicalize_name(candidate.distribution)
    versions = {
        canonicalize_name(each.distribution): each.distribution_version for each in candidates
    }
    versions[canonicalize_name("untaped")] = _version("untaped")
    found: list[str] = []
    described = bool(candidate.requires_dist) or bool(_version(candidate.distribution))
    if described and not plain_requirements(candidate.requires_dist, "untaped"):
        found.append(f"{rule}requires no untaped: declare untaped{floor_for(_version('untaped'))}")
    for line in candidate.requires_dist:
        parsed = parse_requirement(line)
        name = canonicalize_name(parsed.name) if parsed is not None else own
        if parsed is None or parsed.url or name == own or name not in versions:
            continue
        problem = _not_a_floor(parsed, candidate.distribution, versions[name])
        if problem is not None:
            found.append(f"{rule}{line} {problem}")
    owners = fill_owners(plugin.name, composition(), candidates)
    return found + list(_fill_requirements(rule, candidate.requires_dist, owners))


def _not_a_floor(requirement: Requirement, requirer: str, installed: str) -> str | None:
    """What makes ``requirement`` something other than a floor, and the floor to declare.

    The floor offered is the requirement's own lower bound, else ``installed``'s minor.
    """
    specifiers = list(requirement.specifier)
    floors = [each for each in map(floor_of, specifiers) if each is not None]
    floor = f">={floors[0]}" if floors else floor_for(installed)
    declare = f"declare {requirement.name}{floor}"
    if any(map(is_exact, specifiers)):
        if _one_workspace(requirer, requirement.name):
            return None
        return f"is an exact pin to a package not built in this workspace: {declare}"
    if any(map(is_upper, specifiers)):
        return (
            "has an upper limit: one stale plugin would hold back every install "
            f"that has it; {declare}"
        )
    if not any(each.operator in {">=", ">"} for each in specifiers):
        return f"has no floor: {declare}"
    return None


def _version(distribution: str) -> str:
    """The installed version of ``distribution``; ``""`` when it isn't installed."""
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return ""


def _one_workspace(*distributions: str) -> bool:
    """Whether every one of ``distributions`` is an editable install from one uv workspace."""
    roots = {_workspace_root(each) for each in distributions}
    return len(roots) == 1 and None not in roots


def _workspace_root(distribution: str) -> Path | None:
    """The uv workspace an editable install of ``distribution`` was made from, if any.

    Read from its install record (``direct_url.json``): the nearest folder
    above its source that holds a ``pyproject.toml`` declaring
    ``[tool.uv.workspace]``.
    """
    try:
        source = _editable_root(importlib.metadata.distribution(distribution))
    except importlib.metadata.PackageNotFoundError:
        return None
    if source is None:
        return None
    for folder in (source, *source.parents):
        try:
            data = tomllib.loads((folder / "pyproject.toml").read_text(encoding="utf-8"))
        except OSError, UnicodeDecodeError, tomllib.TOMLDecodeError:
            continue
        if "workspace" in data.get("tool", {}).get("uv", {}):
            return folder
    return None


def _fill_requirements(
    rule: str, requires: Sequence[str], owners: Sequence[tuple[str, PluginCandidate | None]]
) -> Iterator[str]:
    """Each owner a plugin class's fills import, required plainly."""
    seen: set[str] = set()
    for _, owner in owners:
        if owner is None or owner.name in seen:
            continue  # no installed owner: the registration row says so
        seen.add(owner.name)
        distribution = str(canonicalize_name(owner.distribution))
        if not plain_requirements(requires, distribution):
            yield (
                f"{rule}fills a contract of {owner.name}, which it does not require "
                f"(add {distribution}{floor_for(owner.distribution_version) or '>=M.m'} "
                "to its dependencies)"
            )
