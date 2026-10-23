"""Where a plugin's code is: its import package and source directory.

Read from what the plugin declares (the module of its app factory, settings
or state model) or from its ``package:SPEC`` entry point, never guessed from
a directory layout. The surface walk, ``plugin list``'s records, ``plugin
new`` and ``plugin check`` all locate a plugin here.
"""

from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path

from untaped.runtime.registry import PluginSpec


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
