"""Structure lint: every capability has the same shape (``docs/conventions.md``).

One parametrized check per built-in capability flags:

- ``errors-module`` — no ``errors.py`` with the capability's error classes;
- ``exception-base`` — an ``Exception`` subclass that is not an
  ``UntapedError`` (``report_errors`` would show a traceback);
- ``exception-name`` — an exception class whose name does not end in ``Error``;
- ``protocol-location`` — a ``Protocol`` defined outside ``application/ports.py``;
- ``port-adapter-clash`` — a port and an infrastructure class share a name;
- ``foreign-section`` — code reads another capability's config section;
- ``settings-not-frozen`` — the profile or state model is mutable;
- ``private-test-import`` — a test imports an ``_``-prefixed module or name.

Existing violations live in ``baselines/structure/<owner>.txt``.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from untaped.bootstrap import BUILTIN_CAPABILITIES
from untaped.errors import UntapedError

REPO_ROOT = Path(__file__).resolve().parents[2]
CAPABILITIES_SRC = REPO_ROOT / "src" / "untaped" / "capabilities"
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


def _runtime_violations(name: str) -> Iterator[str]:
    package = f"untaped.capabilities.{name}"
    ports_module = f"{package}.application.ports"
    ports: set[str] = set()
    adapters: set[str] = set()
    for module in _modules(package):
        for cls in _own_classes(module):
            where = f"{module.__name__}.{cls.__qualname__}"
            if issubclass(cls, BaseException):
                if issubclass(cls, Exception) and not issubclass(cls, UntapedError):
                    yield f"{where}::exception-base"
                if not cls.__name__.endswith("Error"):
                    yield f"{where}::exception-name"
            if getattr(cls, "_is_protocol", False):
                if module.__name__ == ports_module:
                    ports.add(cls.__name__)
                else:
                    yield f"{where}::protocol-location"
            if module.__name__.startswith(f"{package}.infrastructure"):
                adapters.add(cls.__name__)
    for clash in sorted(ports & adapters):
        yield f"{ports_module}.{clash}::port-adapter-clash"


def _source_violations(name: str, section: str) -> Iterator[str]:
    base = CAPABILITIES_SRC / name
    if not (base / "errors.py").is_file():
        yield f"src/untaped/capabilities/{name}/errors.py::errors-module"
    for path in sorted(base.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            func = node.func
            callee = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            first = node.args[0]
            if (
                callee in SECTION_READERS
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and first.value != section
            ):
                yield f"{rel}::foreign-section::{first.value}"


def _settings_violations(spec: Any) -> Iterator[str]:
    for label, model in (("profile", spec.profile_model), ("state", spec.state_model)):
        if model is not None and not model.model_config.get("frozen", False):
            yield f"{model.__module__}.{model.__qualname__}::settings-not-frozen::{label}"


def _private_import_violations(name: str) -> Iterator[str]:
    tests = REPO_ROOT / "tests" / name
    for path in sorted(tests.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("untaped"):
                module = node.module or ""
                private = [part for part in module.split(".") if _private(part)]
                private += [alias.name for alias in node.names if _private(alias.name)]
                for item in private:
                    yield f"{rel}::private-test-import::{module}:{item}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("untaped") and any(
                        _private(part) for part in alias.name.split(".")
                    ):
                        yield f"{rel}::private-test-import::{alias.name}"


def _private(name: str) -> bool:
    return name.startswith("_") and not name.startswith("__")


def collect_violations() -> dict[str, list[str]]:
    found: dict[str, list[str]] = defaultdict(list)
    for spec in BUILTIN_CAPABILITIES:
        found[spec.name].extend(_runtime_violations(spec.name))
        found[spec.name].extend(_source_violations(spec.name, spec.config_section))
        found[spec.name].extend(_settings_violations(spec))
        found[spec.name].extend(_private_import_violations(spec.name))
    return found


def test_capabilities_share_one_structure(baseline: Any) -> None:
    baseline("structure", collect_violations())
