"""Structure from a plugin's files: an ``errors.py``, its own config section, public test imports.

For one plugin package this flags:

- ``errors-module`` — no ``errors.py`` with the plugin's error classes;
- ``foreign-section`` — code reads another plugin's config section;
- ``private-test-import`` — a test imports an ``_``-prefixed module or name
  of ``untaped`` or of the plugin package.

Lines are ``<path>::<rule>::<detail>``; ``# untaped: allow <rule>`` on the
flagged line suppresses one. The structure checks that import the package
(exception classes, ports, frozen settings, setting names) are core's,
behind ``untaped.sdk.testing.check_plugin``.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Sequence
from pathlib import Path

from untaped_lint.domain.allow import allows
from untaped_lint.infrastructure.files import SourceFile, callee, source_files

SECTION_READERS = frozenset({"get_config_section", "section"})


def structure_source_violations(
    plugin: str,
    package: str,
    source_dir: Path,
    files: Sequence[SourceFile],
    *,
    tests_dir: Path | None = None,
) -> list[str]:
    """Violations of plugin ``plugin``, whose code ``package`` is in ``source_dir``.

    ``tests_dir`` adds its tests.
    """
    found = list(_source_violations(source_dir, files, plugin))
    if tests_dir is not None:
        found.extend(_private_import_violations(tests_dir, package))
    return found


def _source_violations(
    source_dir: Path, files: Sequence[SourceFile], section: str
) -> Iterator[str]:
    if not (source_dir / "errors.py").is_file():
        yield f"{source_dir.name}/errors.py::errors-module"
    for source in files:
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
                and not allows(source.lines, node.lineno, "foreign-section")
            ):
                yield f"{rel}::foreign-section::{first.value}"


def _private_import_violations(tests_dir: Path, package: str) -> Iterator[str]:
    checked = ("untaped", package)
    for source in source_files(tests_dir):
        rel = source.path.relative_to(tests_dir.parent).as_posix()
        for node in ast.walk(source.tree):
            if not isinstance(node, ast.Import | ast.ImportFrom) or allows(
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
