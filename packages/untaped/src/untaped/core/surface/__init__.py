"""The public surface snapshot: every promise ``docs/versioning.md`` makes, as lines.

``tests/repo/surface.txt`` lists the surface of untaped and its first-party
plugins, one promise per line, and ``scripts/gen_surface.py`` regenerates it.
Its repo test fails a minor release that removes a stable line;
:func:`untaped.sdk.testing.assert_surface` runs the same walker over one plugin.
Never imported at CLI startup.

The rules every contributor keeps:

- **A line is the unit of promise.** Lines are shaped so that a compatible
  change adds lines and an incompatible change removes one; the guard is two
  set operations (:func:`removed`), with no per-surface compatibility rules.
  What breaks callers when added (a required parameter, positional or field)
  goes into its owner's line; what is free to add (an optional option, a
  choice, an alias, a method) gets a line of its own.
- **The direction an instance travels decides its shape.** What the SDK hands
  to plugins is only called (caller shape); what plugins hand to the SDK, which
  then calls into it, is implemented by them (implementer shape, never
  relaxed). ``Contract`` subclasses are implementer-shaped by rule; any other
  implementer is named in ``_lists.py`` with its reason.
- **A spelling untaped writes outside its own process is promised like a
  visible one**, so a hidden one goes on ``_lists.WRITTEN_OUTSIDE``.
- **One mechanism per policy.** A new kind of surface gets lines here, never a
  snapshot tool of its own.
- **Respelling existing lines bumps** :data:`FORMAT` and teaches
  :func:`upgrade` the old spelling, so a baseline file still compares.
- **A deprecated line names its removal version.** Its mark carries the
  major that removes it (``[deprecated, removed in <major>.0]``);
  :func:`postponements` and :func:`due` are the two questions the release
  check and the changelog build ask of it.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from untaped.core.surface._guard import removed
from untaped.core.surface._lines import (
    FORMAT,
    DeprecatedMark,
    Line,
    LineMark,
    parse,
    render,
    upgrade,
)
from untaped.core.surface._sdk import SurfaceError

if TYPE_CHECKING:
    from cyclopts import App

    from untaped.runtime.registry import CompositionResult, PluginCandidate, PluginSpec

__all__ = [
    "FORMAT",
    "SDK_MODULES",
    "DeprecatedMark",
    "Line",
    "LineMark",
    "Postponement",
    "SurfaceError",
    "due",
    "generate",
    "parse",
    "plugin_lines",
    "plugin_sdk_modules",
    "postponements",
    "removed",
    "render",
    "upgrade",
]

#: Core's SDK modules; each plugin adds its ``api`` and ``testing`` when it has them.
SDK_MODULES = (
    "untaped.sdk",
    "untaped.sdk.contracts",
    "untaped.sdk.testing",
    "untaped.sdk.testing.git",
)


def generate(candidates: Sequence[PluginCandidate]) -> list[Line]:
    """The whole surface: the root composed with ``candidates`` (first-party plugins).

    Raises :class:`RuntimeError` naming any quarantined candidate, and
    :class:`SurfaceError` for anything the walker can't classify.
    """
    from untaped.core.surface._cli import cli_lines  # noqa: PLC0415
    from untaped.core.surface._settings import check_quarantine, settings_lines  # noqa: PLC0415
    from untaped.runtime.bootstrap import (  # noqa: PLC0415
        build_root_app,
        composition,
        kept_composition,
    )

    with kept_composition(quiet=True):
        root = build_root_app(candidates=candidates)
        result = composition()
        check_quarantine(result)
        marks = _plugin_marks(result, root)
        modules: dict[str, LineMark | None] = dict.fromkeys(SDK_MODULES)
        for plugin in result.plugins:
            modules.update(_plugin_modules(plugin.spec, plugin.plugin_ref.entry_point, root))
        lines = cli_lines(root, marks)
        lines.extend(settings_lines(result, tree=root))
        lines.extend(_owned(result, modules, marks))
        lines.extend(_behaviour_lines(result, root))
        lines.extend(_convention_lines())
    return sorted(set(lines))


def plugin_lines(spec: PluginSpec) -> list[Line]:
    """The surface of one plugin, composed alone with the shell.

    Its commands, its ``cfg`` and ``state`` keys, the contracts it owns with
    their payload models, and its ``api`` and ``testing`` modules when it has
    them.
    """
    from untaped.core.surface._cli import subtree_lines  # noqa: PLC0415
    from untaped.core.surface._settings import check_quarantine, settings_lines  # noqa: PLC0415
    from untaped.runtime.bootstrap import (  # noqa: PLC0415
        build_root_app,
        composition,
        kept_composition,
    )
    from untaped.sdk.testing import plugin_candidate  # noqa: PLC0415

    candidate = plugin_candidate(spec)
    with kept_composition(quiet=True):
        root = build_root_app(candidates=[candidate])
        result = composition()
        check_quarantine(result)
        marks = _plugin_marks(result, root)
        modules = _plugin_modules(spec, candidate.target, root)
        lines = subtree_lines(root, spec.name, marks.get(spec.name))
        lines.extend(settings_lines(result, root=False, tree=root))
        lines.extend(_owned(result, modules, marks, core=False))
        lines.extend(_behaviour_lines(result, root, owner=spec.name))
    return sorted(set(lines))


def _plugin_marks(result: CompositionResult, root: App) -> dict[str, LineMark | None]:
    from untaped.core.surface._sdk import line_mark  # noqa: PLC0415

    return {plugin.spec.name: line_mark(plugin.spec.stability, root) for plugin in result.plugins}


def _plugin_modules(spec: PluginSpec, target: object, tree: App) -> dict[str, LineMark | None]:
    """``<package>.api`` and ``<package>.testing`` of a plugin, when importable, with its mark."""
    from untaped.core.surface._sdk import line_mark  # noqa: PLC0415
    from untaped.runtime.plugin_package import candidate_package  # noqa: PLC0415

    package = candidate_package(target)
    if package is None:
        return {}
    return dict.fromkeys(plugin_sdk_modules(package), line_mark(spec.stability, tree))


def plugin_sdk_modules(package: str) -> list[str]:
    """``<package>.api`` and ``<package>.testing``, those that exist: a plugin's Python API."""
    return [
        module
        for module in (f"{package}.api", f"{package}.testing")
        if importlib.util.find_spec(module) is not None
    ]


def _owned(
    result: CompositionResult,
    modules: dict[str, LineMark | None],
    marks: dict[str, LineMark | None],
    *,
    core: bool = True,
) -> list[Line]:
    """The ``sdk`` lines of ``modules``, then each plugin's contracts and ``extensions`` keys."""
    from untaped.core.surface._contracts import (  # noqa: PLC0415
        contract_lines,
        method_mark,
        owned_contracts,
    )
    from untaped.core.surface._sdk import collect_exports, module_lines  # noqa: PLC0415
    from untaped.core.surface._settings import extension_lines  # noqa: PLC0415

    everything = dict(modules)
    if not core:
        everything.update(dict.fromkeys(SDK_MODULES))
    exports = collect_exports(everything)
    lines: list[Line] = []
    for path, mark in sorted(modules.items()):
        lines.extend(module_lines(path, mark, exports, plugin=path not in SDK_MODULES))
    for plugin in result.plugins:
        name = plugin.spec.name
        infos = owned_contracts(plugin.spec)
        ranked = {i.name: {m: method_mark(i, m, marks[name]) for m in i.methods} for i in infos}
        lines.extend(extension_lines(name, ranked))
        lines.extend(contract_lines(name, infos, marks[name], exports.stable_classes))
    return lines


def _behaviour_lines(
    result: CompositionResult, root: App, *, owner: str | None = None
) -> list[Line]:
    """``behaviour <plugin>.<name>`` per listed behaviour (``untaped.<name>`` for core's).

    Only ``owner``'s when it is given. The name is the identity, so the guard
    keeps a behaviour until its major, as it keeps a flag.
    """
    from untaped.core.surface._sdk import line_mark  # noqa: PLC0415
    from untaped.sdk.marks import listed_behaviours  # noqa: PLC0415

    return [
        Line(f"behaviour {name}.{behaviour.name}", line_mark(behaviour.mark, root))
        for name, behaviour in listed_behaviours(result)
        if owner is None or name == owner
    ]


def _convention_lines() -> list[Line]:
    from untaped.core.surface._lists import CONVENTIONS  # noqa: PLC0415

    return [Line(f"convention {entry}") for entry in CONVENTIONS]


@dataclass(frozen=True)
class Postponement:
    """A line whose removal version changed between two files, from ``was`` to ``now``."""

    line: Line
    was: int
    now: int


def postponements(baseline: Sequence[Line], current: Sequence[Line]) -> list[Postponement]:
    """Each line both files date, whose removal version differs (raised or lowered).

    A line undated or absent in ``baseline``, or undated or gone in
    ``current``, gives nothing: no release showed a date that moved. The one
    pairing the release check and the changelog build both read.
    """
    dated = {
        line.identity: mark.removed_in
        for line in baseline
        if (mark := line.deprecation) is not None and mark.removed_in is not None
    }
    found = [
        Postponement(line, dated[line.identity], mark.removed_in)
        for line in current
        if (mark := line.deprecation) is not None
        and mark.removed_in is not None
        and line.identity in dated
        and dated[line.identity] != mark.removed_in
    ]
    return sorted(found, key=lambda item: item.line.identity)


def due(lines: Sequence[Line], major: int) -> list[Line]:
    """The lines marked deprecated with a removal version at or below ``major``, in order."""
    return sorted(
        line
        for line in lines
        if (mark := line.deprecation) is not None
        and mark.removed_in is not None
        and mark.removed_in <= major
    )
