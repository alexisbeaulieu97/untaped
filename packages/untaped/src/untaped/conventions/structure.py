"""Structure lint: every plugin has the same shape (``docs/reference/conventions.md``).

For one plugin package this flags:

- ``exception-base`` — an ``Exception`` subclass that is not an
  ``UntapedError`` (``report_errors`` would show a traceback); ``Warning``
  categories are exempt;
- ``exception-name`` — an exception class whose name does not end in ``Error``;
- ``error-system`` — a plugin error base (no plugin parent) that does
  not set ``system`` (its failures would blame ``untaped``);
- ``protocol-location`` — a ``Protocol`` defined outside an
  ``application/**/ports.py`` module;
- ``port-adapter-clash`` — a port and an infrastructure class share a name;
- ``settings-not-frozen`` — the settings or state model is mutable;
- ``settings-naming`` and ``settings-renames`` — see
  :mod:`untaped.conventions.settings_names`.

These checks import the package and report ``<module>.<class>::<rule>``;
they have no source line, so no inline marker can suppress them. The checks
that read only the plugin's files (``errors-module``, ``foreign-section``,
``private-test-import``) are the lint plugin's.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from untaped.conventions.settings_names import settings_name_violations
from untaped.runtime.registry import PluginSpec, settings_model
from untaped.sdk.errors import UntapedError


def _modules(package: str) -> Iterator[Any]:
    """``package`` and its modules, imported; ``__main__`` modules are skipped.

    A ``__main__`` module runs the program when imported (it may exit).
    """
    root = importlib.import_module(package)
    yield root
    for info in pkgutil.walk_packages(root.__path__, prefix=f"{package}."):
        if info.name.rpartition(".")[2] != "__main__":
            yield importlib.import_module(info.name)


def unimportable(package: str, source_dir: Path) -> str | None:
    """The ``unreadable-file`` violation of the first module of ``package`` Python can't read.

    The class checks import every module; one that isn't UTF-8 or valid
    Python stops them, and is the plugin's one violation.
    """
    try:
        for _ in _modules(package):
            pass
    except SyntaxError as exc:
        where = Path(exc.filename or source_dir)
        if where.is_relative_to(source_dir.parent):
            where = where.relative_to(source_dir.parent)
        reason = (
            "is not UTF-8"
            if "UTF-8" in exc.msg or "codec" in exc.msg
            else f"is not valid Python (line {exc.lineno})"
        )
        return f"{where.as_posix()}::unreadable-file::{reason}"
    return None


def _own_classes(module: Any) -> Iterator[type]:
    for _, value in inspect.getmembers(module, inspect.isclass):
        if value.__module__ == module.__name__:
            yield value


def _runtime_violations(package: str) -> Iterator[str]:
    ports: dict[str, str] = {}
    adapters: set[str] = set()
    for module in _modules(package):
        is_ports = module.__name__.startswith(f"{package}.application") and (
            module.__name__.endswith(".ports")
        )
        for cls in _own_classes(module):
            where = f"{module.__name__}.{cls.__qualname__}"
            if issubclass(cls, BaseException) and not issubclass(cls, Warning):
                if issubclass(cls, Exception) and not issubclass(cls, UntapedError):
                    yield f"{where}::exception-base"
                if not cls.__name__.endswith("Error"):
                    yield f"{where}::exception-name"
                if _unattributed_base(cls, package):
                    yield f"{where}::error-system"
            if getattr(cls, "_is_protocol", False):
                if is_ports:
                    ports[cls.__name__] = where
                else:
                    yield f"{where}::protocol-location"
            if module.__name__.startswith(f"{package}.infrastructure"):
                adapters.add(cls.__name__)
    for clash in sorted(ports.keys() & adapters):
        yield f"{ports[clash]}::port-adapter-clash"


def _unattributed_base(cls: type, package: str) -> bool:
    """A plugin's own ``*Error`` base (no plugin parent) that sets no ``system``."""
    if not issubclass(cls, UntapedError):
        return False
    own_parents = [base for base in cls.__mro__[1:] if base.__module__.startswith(package)]
    return not own_parents and "system" not in vars(cls)


def _settings_violations(spec: PluginSpec) -> Iterator[str]:
    for label, model in (("settings", spec.settings), ("state", spec.state)):
        if model is not None and not model.model_config.get("frozen", False):
            yield f"{model.__module__}.{model.__qualname__}::settings-not-frozen::{label}"


def structure_class_violations(spec: PluginSpec, package: str, source_dir: Path) -> list[str]:
    """The checks that import the package or read its models: they need the installed code."""
    return [
        *_runtime_violations(package),
        *_settings_violations(spec),
        *settings_name_violations(
            spec.name, settings_model(spec), source_dir.parent, state=spec.state
        ),
    ]
