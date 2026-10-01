"""Structure lint: every capability has the same shape (``docs/conventions.md``).

For one capability package this flags:

- ``errors-module`` — no ``errors.py`` with the capability's error classes;
- ``exception-base`` — an ``Exception`` subclass that is not an
  ``UntapedError`` (``report_errors`` would show a traceback); ``Warning``
  categories are exempt;
- ``exception-name`` — an exception class whose name does not end in ``Error``;
- ``error-system`` — a capability error base (no capability parent) that does
  not set ``system`` (its failures would blame ``untaped``);
- ``protocol-location`` — a ``Protocol`` defined outside an
  ``application/**/ports.py`` module;
- ``port-adapter-clash`` — a port and an infrastructure class share a name;
- ``foreign-section`` — code reads another capability's config section;
- ``settings-not-frozen`` — the profile or state model is mutable;
- ``private-test-import`` — a test imports an ``_``-prefixed module or name
  of ``untaped`` or of the capability package.

The class checks import the package and report ``<module>.<class>::<rule>``;
they have no source line, so no inline marker can suppress them. The source
checks report ``<path>::<rule>::<detail>`` and honour ``# untaped: allow``.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from untaped.capabilities.registry import CapabilitySpec
from untaped.conventions.allow import allowed
from untaped.conventions.source import callee, source_files
from untaped.errors import UntapedError

SECTION_READERS = frozenset({"get_config_section", "section"})


def _modules(package: str) -> Iterator[Any]:
    root = importlib.import_module(package)
    yield root
    for info in pkgutil.walk_packages(root.__path__, prefix=f"{package}."):
        yield importlib.import_module(info.name)


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
    """A capability's own ``*Error`` base (no capability parent) that sets no ``system``."""
    if not issubclass(cls, UntapedError):
        return False
    own_parents = [base for base in cls.__mro__[1:] if base.__module__.startswith(package)]
    return not own_parents and "system" not in vars(cls)


def _source_violations(source_dir: Path, section: str) -> Iterator[str]:
    if not (source_dir / "errors.py").is_file():
        yield f"{source_dir.name}/errors.py::errors-module"
    for source in source_files(source_dir):
        rel = source.path.relative_to(source_dir.parent).as_posix()
        for node in ast.walk(source.tree):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            first = node.args[0]
            if (
                callee(node) in SECTION_READERS
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and first.value != section
                and not allowed(source.lines, node.lineno, "foreign-section")
            ):
                yield f"{rel}::foreign-section::{first.value}"


def _settings_violations(spec: CapabilitySpec) -> Iterator[str]:
    for label, model in (("profile", spec.profile_model), ("state", spec.state_model)):
        if model is not None and not model.model_config.get("frozen", False):
            yield f"{model.__module__}.{model.__qualname__}::settings-not-frozen::{label}"


def _private_import_violations(tests_dir: Path, package: str) -> Iterator[str]:
    checked = ("untaped", package)
    for source in source_files(tests_dir):
        rel = source.path.relative_to(tests_dir.parent).as_posix()
        for node in ast.walk(source.tree):
            if not isinstance(node, ast.Import | ast.ImportFrom) or allowed(
                source.lines, node.lineno, "private-test-import"
            ):
                continue
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(checked):
                module = node.module or ""
                private = [part for part in module.split(".") if _private(part)]
                private += [alias.name for alias in node.names if _private(alias.name)]
                for item in private:
                    yield f"{rel}::private-test-import::{module}:{item}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(checked) and any(
                        _private(part) for part in alias.name.split(".")
                    ):
                        yield f"{rel}::private-test-import::{alias.name}"


def _private(name: str) -> bool:
    return name.startswith("_") and not name.startswith("__")


def structure_violations(
    spec: CapabilitySpec, package: str, source_dir: Path, *, tests_dir: Path | None = None
) -> list[str]:
    """Violations of the capability ``spec`` whose code is ``package`` in ``source_dir``.

    ``tests_dir``, when given, is scanned for private imports of ``untaped``
    or ``package``.
    """
    found = [
        *_runtime_violations(package),
        *_source_violations(source_dir, spec.config_section),
        *_settings_violations(spec),
    ]
    if tests_dir is not None:
        found.extend(_private_import_violations(tests_dir, package))
    return found
