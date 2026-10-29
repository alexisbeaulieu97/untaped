"""Where a plugin's code is: its import package and source directory.

Read from its entry point's target (the plugin class's module), never guessed
from a directory layout. The surface walk, ``plugin list``'s records, ``plugin
new`` and the plugin checks (:mod:`untaped.core.plugin_check`) all locate a plugin here.
"""

from __future__ import annotations

from importlib.machinery import ModuleSpec
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untaped.runtime.composed import ComposedPlugin


def candidate_package(target: object) -> str | None:
    """The plugin package of a candidate ``target``, or ``None`` when unresolvable.

    A string target's module is the package (``untaped_acme:Acme``) or a
    module in it (a plugin class in its leaf ``untaped_acme.plugin``); a class
    given directly is read by its module.
    """
    if isinstance(target, str):
        return _package_of_module(target.partition(":")[0]) if ":" in target else None
    if isinstance(target, type):
        return _package_of_module(target.__module__)
    return None


def package_of(plugin: ComposedPlugin) -> tuple[str, Path]:
    """The package owning ``plugin`` and its source directory.

    That is the module of its class (its entry point's target) when it is a
    package, else the module's parent package.
    """
    module = plugin.decl.target.partition(":")[0]
    found = _find(module)
    if found is not None and found.submodule_search_locations:
        return module, Path(found.submodule_search_locations[0])
    package = module.rpartition(".")[0]
    if not package:
        raise LookupError(f"plugin {plugin.name!r}: its class is not defined in a package")
    return package, _source_dir(package)


def _package_of_module(module: str) -> str:
    """``module`` when it is a package or can't be found, else the package holding it.

    It reads the files under the top package's folder, so no plugin code runs.
    """
    top, *rest = module.split(".")
    try:
        found = find_spec(top)
    except Exception:  # a broken finder or name: say the target as written
        return module
    if found is None or not found.submodule_search_locations or not rest:
        return module
    folders = [Path(each) for each in found.submodule_search_locations]
    *packages, leaf = rest
    for part in packages:
        folders = [folder / part for folder in folders if (folder / part).is_dir()]
        if not folders:
            return module
    # the last part is a package when it is a folder, else a module in the one before
    if any((folder / leaf).is_dir() for folder in folders):
        return module
    return module.rpartition(".")[0]


def _find(module: str) -> ModuleSpec | None:
    """``module``'s spec, ``None`` when it has none (a module made at run time)."""
    try:
        return find_spec(module)
    except ValueError:
        return None


def _source_dir(package: str) -> Path:
    found = _find(package)
    if found is None or not found.submodule_search_locations:
        raise LookupError(f"package {package!r} has no source directory")
    return Path(found.submodule_search_locations[0])
