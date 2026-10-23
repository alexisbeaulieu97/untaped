"""Convention checks for plugins (the repo's tests run them on the root app too).

See ``docs/reference/conventions.md#enforcement``.

Each check reads one installed package: its command subtree from the real
composition and its own source files, wherever they are installed, so a
third-party plugin is checked exactly like a first-party one. Plugin tests call
:func:`untaped.sdk.testing.check_conventions`; this package is internal.
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

from cyclopts import App
from packaging.utils import canonicalize_name

from untaped.conventions.guides import guide_violations
from untaped.conventions.help_tree import help_tree_violations
from untaped.conventions.stability import (
    deprecation_text_violations,
    stability_violations,
    undated_violations,
)
from untaped.conventions.structure import structure_class_violations, unimportable
from untaped.runtime.bootstrap import build_root_app, composition
from untaped.runtime.plugin_package import package_of
from untaped.runtime.registry import (
    PluginCandidate,
    PluginSpec,
    discover_candidates,
    mentions_extra,
    owner_requirement,
    parse_requirement,
)
from untaped.sdk.plugin_records import Plugin, plugin_records


def plugin_violations(
    name: str,
    *,
    tests_dir: Path | None = None,
    candidates: Sequence[PluginCandidate] | None = None,
) -> list[str]:
    """Every convention violation of the installed plugin ``name``.

    Builds the root once (from discovered candidates), finds the
    registered plugin, and runs :func:`core_plugin_violations`' checks plus,
    when ``untaped[lint]`` is installed, the file rules it now owns
    (:func:`file_lines`); the private-test-import rule runs only when
    ``tests_dir`` is given. ``candidates`` replaces entry-point discovery (as in
    :func:`untaped.runtime.bootstrap.compose_root`), so a test can check a provider
    that is not installed. Lines are ``<where>::<rule>::<detail>``, sorted. A
    quarantined plugin's one violation is the reason composition refused it.
    """
    found = _checked(name, candidates)
    if isinstance(found, list):
        return found
    return sorted([*_core_lines(found), *_file_lines(found, tests_dir)])


def core_plugin_violations(
    name: str, *, candidates: Sequence[PluginCandidate] | None = None
) -> list[str]:
    """The violations only the composed root can tell: what ``check_plugin`` reports.

    Plugin and distribution names, provides requirements, the help tree,
    guides and map lines, stability marks and structure's class checks
    (exceptions, ports, frozen settings, setting names). The rules that read
    the plugin's files, the import boundary among them, are lint's and are
    not run here. Lines and a quarantined
    plugin's one violation are as :func:`plugin_violations` gives them.
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
    spec: PluginSpec
    own: PluginCandidate | None
    candidates: Sequence[PluginCandidate]
    package: str
    source_dir: Path


def _checked(name: str, candidates: Sequence[PluginCandidate] | None) -> _Checked | list[str]:
    """The composed plugin ``name``, or a quarantined plugin's one violation."""
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
    unreadable = unimportable(package, source_dir)
    if unreadable is not None:
        # The class checks can't import the plugin; the file is its one violation.
        return [unreadable]
    return _Checked(name, root, spec, own, candidates, package, source_dir)


def _core_lines(checked: _Checked) -> list[str]:
    spec, name = checked.spec, checked.name
    commands = [] if spec.app_factory is None else [name]  # its mounted subtree
    return [
        *_name_violations(name, checked.package, checked.own),
        *_provides_violations(spec, checked.own, checked.candidates),
        *help_tree_violations(checked.root, commands),
        *guide_violations(checked.root, composition(), spec),
        *stability_violations(checked.root, composition(), commands, spec=spec),
        *undated_violations(spec),
        *_sdk_deprecations(checked.package),
        *structure_class_violations(spec, checked.package, checked.source_dir),
    ]


def _sdk_deprecations(package: str) -> list[str]:
    """``deprecation_text_violations`` of the plugin's ``api`` and ``testing`` modules."""
    from untaped.core.surface import plugin_sdk_modules  # noqa: PLC0415 - surface imports this

    return [
        line
        for path in plugin_sdk_modules(package)
        for line in deprecation_text_violations(importlib.import_module(path))
    ]


def _file_lines(checked: _Checked, tests_dir: Path | None) -> list[str]:
    lines = file_lines(
        checked.package,
        checked.source_dir,
        plugin=checked.name,
        tests_dir=tests_dir,
        plugins=plugin_records(composition(), checked.candidates),
    )
    return [] if lines is None else lines


FILE_RULES_MISSING = "the file conventions moved to untaped[lint] and were not run"
"""What the deprecated checks say when ``untaped[lint]`` is not installed."""


def file_rules_installed() -> bool:
    """Whether ``untaped[lint]``, which owns the file rules, is importable."""
    return find_spec("untaped_lint") is not None


def file_lines(
    package: str,
    source_dir: Path,
    *,
    plugin: str,
    tests_dir: Path | None = None,
    plugins: Sequence[Plugin] = (),
) -> list[str] | None:
    """The file rules' lines, from the lint plugin; ``None`` when it is not installed.

    The rules that read only a plugin's files moved to ``untaped[lint]``; the
    deprecated ``plugin check`` and ``check_conventions`` reach them here, by
    name, until both go at 12.0. This is the one place core calls a plugin.
    ``plugins`` are every composed plugin's records, which the import
    boundary reads.
    """
    try:
        conventions = importlib.import_module("untaped_lint.providers.conventions")
    except ImportError:
        return None
    lines: list[str] = conventions.file_lines(
        package, source_dir, plugin=plugin, tests_dir=tests_dir, plugins=plugins
    )
    return lines


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
        for parsed in (parse_requirement(line) for line in requires)
        if parsed is not None
        and canonicalize_name(parsed.name) == "untaped"
        and not mentions_extra(str(parsed.marker or ""))
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
