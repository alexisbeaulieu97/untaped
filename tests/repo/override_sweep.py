"""The override sweep behind ``test_overrides_are_declared``: run it as a script.

Imports every module of each first-party package and example, and prints one
line per member a class overrides on an exported SDK class that is neither a
listed hook nor on an implementer (nothing printed: all declared).
"""

from __future__ import annotations

import dataclasses
import importlib
import importlib.util
import pkgutil
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

from untaped.contracts import Contract
from untaped.surface import SDK_MODULES
from untaped.surface._lists import HOOKS, IMPLEMENTERS
from untaped.surface._sdk import Exports, _split_mro, collect_exports

REPO_ROOT = Path(__file__).resolve().parents[2]


def _first_party_packages() -> Iterator[tuple[str, Path]]:
    sources = [*(REPO_ROOT / "packages").glob("*/src"), *(REPO_ROOT / "examples").glob("*/src")]
    for src in sorted(sources):
        for package in sorted(src.iterdir()):
            if (package / "__init__.py").is_file():
                yield package.name, src


def _modules() -> Iterator[ModuleType]:
    packages = list(_first_party_packages())
    for src in sorted({src for _name, src in packages} - set(map(Path, sys.path))):
        sys.path.insert(0, str(src))
    for name, _src in packages:
        package = importlib.import_module(name)
        yield package
        for info in pkgutil.walk_packages(package.__path__, f"{name}."):
            yield importlib.import_module(info.name)


def _exports() -> Exports:
    modules: dict[str, None] = dict.fromkeys(SDK_MODULES)
    for name, _src in _first_party_packages():
        for module in (f"{name}.api", f"{name}.testing"):
            if importlib.util.find_spec(module) is not None:
                modules[module] = None
    return collect_exports(modules)


def _defines(cls: type, name: str) -> bool:
    """Whether ``cls``'s own (untaped) classes define ``name`` and no foreign base does."""
    own, outside = _split_mro(cls)
    return name not in outside and any(name in vars(klass) for klass in own)


def _fields(cls: type) -> set[str]:
    found = set(getattr(cls, "model_fields", {}) or {})
    if dataclasses.is_dataclass(cls):
        found |= {f.name for f in dataclasses.fields(cls)}
    return found


def undeclared() -> list[str]:
    """``<class>.<member> overrides <exported base>`` for each undeclared override."""
    seen: set[type] = set()
    for module in _modules():
        seen.update(
            value
            for value in vars(module).values()
            if isinstance(value, type) and value.__module__ == module.__name__
        )
    exports = _exports()
    paths: dict[type, list[str]] = {}
    for path, cls in exports.classes.items():
        paths.setdefault(cls, []).append(path)
    found: set[str] = set()
    for cls in seen:
        fields = _fields(cls)
        for base in cls.__mro__[1:]:
            if base not in paths or issubclass(base, Contract):
                continue
            if any(path in IMPLEMENTERS for path in paths[base]):
                continue
            hooks = {
                name
                for path, names in HOOKS.items()
                if exports.classes.get(path) in base.__mro__
                for name in names
            }
            for name in vars(cls):
                if name.startswith("_") or name in fields or name in hooks:
                    continue
                if _defines(base, name):
                    found.add(
                        f"{cls.__module__}.{cls.__qualname__}.{name} overrides {paths[base][0]}"
                    )
    return sorted(found)


if __name__ == "__main__":
    for line in undeclared():
        print(line)
