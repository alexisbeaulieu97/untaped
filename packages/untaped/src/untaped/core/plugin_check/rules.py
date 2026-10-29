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
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from cyclopts import App
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
from untaped.runtime.registry import (
    PluginCandidate,
    PluginSpec,
    discover_candidates,
    mentions_extra,
    owner_requirement,
    parse_requirement,
    plain_requirements,
)


def plugin_violations(
    name: str, *, candidates: Sequence[PluginCandidate] | None = None
) -> list[str]:
    """Every convention violation only the composed root can tell: what ``check_plugin`` reports.

    Plugin and distribution names, provides requirements, the help tree,
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
    spec: PluginSpec
    own: PluginCandidate
    candidates: Sequence[PluginCandidate]
    package: str
    source_dir: Path


def _checked(name: str, candidates: Sequence[PluginCandidate] | None) -> _Checked | list[str]:
    """The composed plugin ``name``, or a quarantined plugin's one violation."""
    candidates = list(discover_candidates()) if candidates is None else candidates
    root = build_root_app(candidates=candidates)
    spec = next(
        (
            plugin.spec
            for plugin in composition().plugins
            # the root plugin is untaped itself, not one installed into it
            if plugin.spec.name == name and not plugin.root
        ),
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
    if own is None:  # a composed plugin is one of the candidates
        raise LookupError(f"no candidate named {name!r}")
    package, source_dir = package_of(spec, own.target)
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
        *undated_violations(spec, checked.own),
        *_sdk_deprecations(checked.package),
        *structure_class_violations(spec, checked.package, checked.source_dir),
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

    A plugin with ``provides`` or ``fills`` requires ``untaped`` with a version
    range. A plugin class requires each owner whose contract a fill subclasses
    in its plain dependencies, with a range (``untaped-<owner>>=M,<M+1``),
    since it imports the owner; an 11.x spec requires each owner under an
    extra named after it. The registry checks the installed owner against
    that range (``owner-out-of-range``).
    """
    owners = fill_owners(spec.name, composition(), candidates)
    if not spec.provides and not owners:
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
    if owners:
        return found + list(_fill_requirements(rule, requires, owners))
    distributions = {c.name: c.distribution for c in candidates}
    for owner in sorted(spec.provides):
        distribution = str(canonicalize_name(distributions.get(owner, f"untaped-{owner}")))
        requirement = owner_requirement(requires, owner, distribution)
        if requirement is None:
            found.append(
                f"{rule}provides for {owner} but has no {owner!r} extra requiring {distribution}"
            )
        elif not (requirement.specifier or requirement.url):
            what = (
                "requires"
                if plain_requirements(requires, distribution)
                else (f"its {owner!r} extra requires")
            )
            found.append(f"{rule}{what} {distribution} with no version range")
    return found


def _fill_requirements(
    rule: str, requires: Sequence[str], owners: Sequence[tuple[str, PluginCandidate | None]]
) -> Iterator[str]:
    """Each owner a plugin class's fills import, required plainly with a range."""
    seen: set[str] = set()
    for _, owner in owners:
        if owner is None or owner.name in seen:
            continue  # no installed owner: the registration row says so
        seen.add(owner.name)
        distribution = str(canonicalize_name(owner.distribution))
        plain = plain_requirements(requires, distribution)
        if not plain:
            yield (
                f"{rule}fills a contract of {owner.name}, which it does not require "
                f"(add {distribution}>=M,<M+1 to its dependencies)"
            )
        elif not any(parsed.specifier or parsed.url for parsed in plain):
            yield f"{rule}requires {distribution} with no version range"
