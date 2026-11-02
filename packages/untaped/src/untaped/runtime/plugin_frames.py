"""Which composed plugin's code a module or file is: the run-time reader of plugin ownership.

A plugin owns the modules under its entry point's package; where packages
nest or share a namespace, the deepest one wins (:func:`untaped.runtime.plugin_uses.owner_of`,
the rule doctor's import uses follow). Core's own plugin and plugins released
with what they use (``PluginDecl.lockstep``) own nothing here: what they
call is untaped's own business. The recorder of run-time uses
(:mod:`untaped.core.run_uses`) and the error boundary's translations
(:mod:`untaped.runtime.missing_names`) read it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untaped.runtime.declaration import PluginDecl
    from untaped.runtime.registry import CompositionResult, PluginCandidate


@dataclass(frozen=True)
class PluginAt:
    """A composed plugin, as the code it owns is attributed to it."""

    name: str
    distribution: str
    version: str
    #: Its ``Requires-Dist`` lines, which the floor rule reads.
    requires_dist: tuple[str, ...]


#: A module's file -> its name, for the modules imported when it was last filled.
_FILES: dict[str, str] = {}
#: The composed plugins last read, and each by its entry point's package.
_PACKAGES: list[tuple[tuple[PluginDecl, ...], dict[str, PluginDecl]]] = []


def plugin_at(module: str, result: CompositionResult) -> PluginAt | None:
    """The composed plugin of ``result`` whose code ``module`` is; ``None`` for core or nobody."""
    from untaped.runtime.plugin_uses import owner_of  # noqa: PLC0415 - read on a warning or error

    plugin = owner_of(module, _packages(result))
    if plugin is None or plugin.lockstep or plugin.root:
        return None
    candidate = _candidate(result, plugin.name)
    if candidate is None:
        return None
    return PluginAt(
        plugin.name,
        candidate.distribution,
        candidate.distribution_version,
        tuple(candidate.requires_dist),
    )


def module_of(filename: str) -> str | None:
    """The imported module whose file ``filename`` is."""
    if filename not in _FILES:
        _FILES.clear()
        for name, module in list(sys.modules.items()):
            path = getattr(module, "__file__", None)
            if isinstance(path, str):
                _FILES[path] = name
    return _FILES.get(filename)


def forget() -> None:
    """Forget the files and packages read (tests, embedding)."""
    _FILES.clear()
    _PACKAGES.clear()


def _candidate(result: CompositionResult, name: str) -> PluginCandidate | None:
    return next((each for each in result.candidates if each.name == name), None)


def _packages(result: CompositionResult) -> dict[str, PluginDecl]:
    """Each composed plugin by its entry point's package, read once per composition."""
    plugins = result.mount.declaration.plugins
    if not _PACKAGES or _PACKAGES[0][0] is not plugins:
        from untaped.runtime.plugin_package import candidate_package  # noqa: PLC0415

        found: dict[str, PluginDecl] = {}
        for plugin in plugins:
            package = candidate_package(plugin.target)
            if package is not None:
                found.setdefault(package, plugin)
        _PACKAGES[:] = [(plugins, found)]
    return _PACKAGES[0][1]
