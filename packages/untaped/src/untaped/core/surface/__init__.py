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

    from untaped.runtime.composed import ComposedPlugin
    from untaped.runtime.registry import (
        CompositionResult,
        PluginCandidate,
    )

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
    "untaped.sdk.git",
    "untaped.sdk.health",
    "untaped.sdk.network",
    "untaped.sdk.projects",
    "untaped.sdk.prompt",
    "untaped.sdk.storage",
    "untaped.sdk.testing",
    "untaped.sdk.testing.git",
)


def generate(candidates: Sequence[PluginCandidate]) -> list[Line]:
    """The whole surface: the lines of every plugin composed with ``candidates``, core's included.

    Raises :class:`RuntimeError` naming any quarantined candidate, and
    :class:`SurfaceError` for anything the walker can't classify or no
    composed plugin owns.
    """
    from untaped.core.surface._settings import check_quarantine  # noqa: PLC0415
    from untaped.runtime.bootstrap import (  # noqa: PLC0415
        build_root_app,
        composition,
        kept_composition,
    )

    with kept_composition():
        root = build_root_app(candidates=candidates)
        result = composition()
        check_quarantine(result)
        composed = _Composed(result, root)
        composed.check_owned()
        lines = [line for plugin in result.plugins for line in composed.lines(plugin)]
    return sorted(set(lines))


def plugin_lines(candidate: PluginCandidate) -> list[Line]:
    """The surface of one plugin, its ``candidate``, composed alone with core.

    Its commands, its ``cfg`` and ``state`` keys, the contracts it owns with
    their payload models, its record kinds, and its ``api`` and ``testing``
    modules when it has them: the lines :func:`generate` takes from every
    composed plugin.
    """
    from untaped.core.surface._settings import check_quarantine  # noqa: PLC0415
    from untaped.runtime.bootstrap import (  # noqa: PLC0415
        build_root_app,
        composition,
        kept_composition,
    )

    with kept_composition():
        root = build_root_app(candidates=[candidate])
        result = composition()
        check_quarantine(result)
        plugin = next(each for each in result.plugins if each.name == candidate.name)
        lines = _Composed(result, root).lines(plugin)
    return sorted(set(lines))


class _Composed:
    """One composition's surface, read plugin by plugin.

    What several plugins' lines share is read once: each plugin's mark, the
    exports of every composed plugin's Python modules, the behaviours and the
    record lines by owner. The root plugin (core) differs only where it is the
    root: its commands mount at the root, where the root's own options are;
    the ``convention`` lines, names core finds, are its own; and its Python
    API is the SDK.
    """

    def __init__(self, result: CompositionResult, root: App) -> None:
        from untaped.core.surface._records import record_lines  # noqa: PLC0415
        from untaped.core.surface._sdk import collect_exports, line_mark  # noqa: PLC0415
        from untaped.sdk.marks import listed_behaviours  # noqa: PLC0415

        self.result = result
        self.root = root
        self.marks: dict[str, LineMark | None] = {
            plugin.name: line_mark(plugin.stability, root) for plugin in result.plugins
        }
        self.modules = {plugin.name: self._modules_of(plugin) for plugin in result.plugins}
        everything: dict[str, LineMark | None] = {}
        for modules in self.modules.values():
            everything.update(modules)
        self.exports = collect_exports(everything)
        # ``behaviour <plugin>.<name>``: the name is the identity, so the guard
        # keeps a behaviour until its major, as it keeps a flag.
        self.behaviours: dict[str, list[Line]] = {}
        for name, behaviour in listed_behaviours(result):
            line = Line(f"behaviour {name}.{behaviour.name}", line_mark(behaviour.mark, root))
            self.behaviours.setdefault(name, []).append(line)
        self.records = record_lines(result, self.marks)

    def check_owned(self) -> None:
        """Raise :class:`SurfaceError` for a root command, behaviour or record no plugin owns.

        The whole surface is the union of the plugins' lines, so a line with
        no owner would drop out of it unseen.
        """
        from untaped.sdk.marks import children  # noqa: PLC0415

        owned: set[str] = set()
        for plugin in self.result.plugins:
            owned |= self._words(plugin)
        loose = sorted(name for name, _ in children(self.root, resolve=True) if name not in owned)
        for area, owners in (("behaviour", self.behaviours), ("record", self.records)):
            loose.extend(sorted(f"{area} owner {o}" for o in owners if o not in self.marks))
        if loose:
            raise SurfaceError(f"no composed plugin owns: {', '.join(loose)}")

    def lines(self, plugin: ComposedPlugin) -> list[Line]:
        """Every line ``plugin`` owns.

        Its commands, settings and state, Python API, contracts, behaviours
        and record kinds.
        """
        from untaped.core.surface._cli import root_plugin_lines, subtree_lines  # noqa: PLC0415
        from untaped.core.surface._contracts import (  # noqa: PLC0415
            contract_lines,
            method_mark,
            owned_contracts,
        )
        from untaped.core.surface._sdk import module_lines  # noqa: PLC0415
        from untaped.core.surface._settings import extension_lines, settings_lines  # noqa: PLC0415

        name = plugin.name
        mark = self.marks[name]
        if plugin.root:
            lines = root_plugin_lines(self.root, self._words(plugin), mark)
            lines.extend(_convention_lines())
        else:
            lines = subtree_lines(self.root, name, mark)
        lines.extend(settings_lines(self.result, owner=name, tree=self.root))
        for path, module_mark in sorted(self.modules[name].items()):
            lines.extend(module_lines(path, module_mark, self.exports, plugin=not plugin.root))
        infos = owned_contracts(plugin)
        ranked = {i.name: {m: method_mark(i, m, mark) for m in i.methods} for i in infos}
        lines.extend(extension_lines(name, ranked))
        lines.extend(contract_lines(name, infos, mark, self.exports.stable_classes))
        lines.extend(self.behaviours.get(name, ()))
        lines.extend(self.records.get(name, ()))
        return lines

    def _words(self, plugin: ComposedPlugin) -> set[str]:
        """The root words ``plugin``'s commands take: its name, or the root plugin's own words."""
        if plugin.root:
            return self.result.mount.declaration.plugin(plugin.name).command_words()
        return {plugin.name}

    def _modules_of(self, plugin: ComposedPlugin) -> dict[str, LineMark | None]:
        """The plugin's Python API, with its mark.

        The SDK for the root, else the ``api`` and ``testing`` of the package
        its declaration names.
        """
        mark = self.marks[plugin.name]
        if plugin.root:
            return dict.fromkeys(SDK_MODULES, mark)
        return dict.fromkeys(plugin_sdk_modules(plugin.decl.package), mark)


def plugin_sdk_modules(package: str) -> list[str]:
    """``<package>.api`` and ``<package>.testing``, those that exist: a plugin's Python API."""
    return [
        module
        for module in (f"{package}.api", f"{package}.testing")
        if importlib.util.find_spec(module) is not None
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
