"""The public surface snapshot: every promise ``docs/versioning.md`` makes, as lines.

``tests/repo/surface.txt`` lists the surface of untaped and its first-party
plugins, one promise per line, and ``scripts/gen_surface.py`` regenerates it.
Its repo test fails a minor release that removes a stable line;
:func:`untaped.testing.assert_surface` runs the same walker over one plugin.
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
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from typing import TYPE_CHECKING

from untaped.surface._guard import removed
from untaped.surface._lines import FORMAT, Line, parse, render, upgrade
from untaped.surface._sdk import SurfaceError

if TYPE_CHECKING:
    from untaped.plugins.registry import CompositionResult, PluginCandidate, PluginSpec
    from untaped.surface._lines import MarkName

__all__ = [
    "FORMAT",
    "SDK_MODULES",
    "Line",
    "SurfaceError",
    "generate",
    "parse",
    "plugin_lines",
    "removed",
    "render",
    "upgrade",
]

#: Core's SDK modules; each plugin adds its ``api`` and ``testing`` when it has them.
SDK_MODULES = ("untaped.contracts", "untaped.sdk", "untaped.testing", "untaped.testing.git")


def generate(candidates: Sequence[PluginCandidate]) -> list[Line]:
    """The whole surface: the root composed with ``candidates`` (first-party plugins).

    Raises :class:`RuntimeError` naming any quarantined candidate, and
    :class:`SurfaceError` for anything the walker can't classify.
    """
    from untaped.bootstrap import build_root_app, composition, kept_composition  # noqa: PLC0415
    from untaped.surface._cli import cli_lines  # noqa: PLC0415
    from untaped.surface._settings import check_quarantine, settings_lines  # noqa: PLC0415

    with kept_composition(quiet=True):
        root = build_root_app(candidates=candidates)
        result = composition()
        check_quarantine(result)
        marks = _plugin_marks(result)
        modules: dict[str, MarkName | None] = dict.fromkeys(SDK_MODULES)
        for plugin in result.plugins:
            modules.update(_plugin_modules(plugin.spec, plugin.plugin_ref.entry_point))
        lines = cli_lines(root, marks)
        lines.extend(settings_lines(result))
        lines.extend(_owned(result, modules, marks))
        lines.extend(_convention_lines())
    return sorted(set(lines))


def plugin_lines(spec: PluginSpec) -> list[Line]:
    """The surface of one plugin, composed alone with the shell.

    Its commands, its ``cfg`` and ``state`` keys, the contracts it owns with
    their payload models, and its ``api`` and ``testing`` modules when it has
    them.
    """
    from untaped.bootstrap import build_root_app, composition, kept_composition  # noqa: PLC0415
    from untaped.surface._cli import subtree_lines  # noqa: PLC0415
    from untaped.surface._settings import check_quarantine, settings_lines  # noqa: PLC0415
    from untaped.testing import plugin_candidate  # noqa: PLC0415

    candidate = plugin_candidate(spec)
    with kept_composition(quiet=True):
        root = build_root_app(candidates=[candidate])
        result = composition()
        check_quarantine(result)
        marks = _plugin_marks(result)
        modules = _plugin_modules(spec, candidate.target)
        lines = subtree_lines(root, spec.name, marks.get(spec.name))
        lines.extend(settings_lines(result, root=False))
        lines.extend(_owned(result, modules, marks, core=False))
    return sorted(set(lines))


def _plugin_marks(result: CompositionResult) -> dict[str, MarkName | None]:
    from untaped.surface._sdk import mark_name  # noqa: PLC0415

    return {plugin.spec.name: mark_name(plugin.spec.stability) for plugin in result.plugins}


def _plugin_modules(spec: PluginSpec, target: object) -> dict[str, MarkName | None]:
    """``<package>.api`` and ``<package>.testing`` of a plugin, when importable."""
    from untaped.conventions import candidate_package  # noqa: PLC0415
    from untaped.surface._sdk import mark_name  # noqa: PLC0415

    package = candidate_package(target)
    if package is None:
        return {}
    found: dict[str, MarkName | None] = {}
    for module in (f"{package}.api", f"{package}.testing"):
        if importlib.util.find_spec(module) is not None:
            found[module] = mark_name(spec.stability)
    return found


def _owned(
    result: CompositionResult,
    modules: dict[str, MarkName | None],
    marks: dict[str, MarkName | None],
    *,
    core: bool = True,
) -> list[Line]:
    """The ``sdk`` lines of ``modules``, then each plugin's contracts and ``extensions`` keys."""
    from untaped.surface._contracts import contract_lines, owned_contracts  # noqa: PLC0415
    from untaped.surface._sdk import collect_exports, module_lines  # noqa: PLC0415
    from untaped.surface._settings import extension_lines  # noqa: PLC0415

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
        lines.extend(extension_lines(name, {i.name: sorted(i.methods) for i in infos}, marks[name]))
        lines.extend(contract_lines(name, infos, marks[name], exports.stable_classes))
    return lines


def _convention_lines() -> list[Line]:
    from untaped.surface._lists import CONVENTIONS  # noqa: PLC0415

    return [Line(f"convention {entry}") for entry in CONVENTIONS]
