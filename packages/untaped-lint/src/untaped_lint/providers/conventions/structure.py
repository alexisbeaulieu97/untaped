"""Structure from a plugin's files: an ``errors.py`` and public test imports.

For one plugin package this flags:

- ``errors-module`` — no ``errors.py`` with the plugin's error classes;
- ``private-test-import`` — a test imports an ``_``-prefixed module or name
  of ``untaped`` or of the plugin package.

Lines are ``<path>::<rule>::<detail>``; ``# untaped: allow <rule>`` on the
flagged line suppresses one. The structure checks that import the package
(exception classes, ports, frozen settings, setting names) are core's,
behind ``untaped.sdk.testing.check_plugin``.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

from untaped_lint.domain.allow import allows
from untaped_lint.infrastructure.files import source_files


def structure_source_violations(
    package: str, source_dir: Path, *, tests_dir: Path | None = None
) -> list[str]:
    """Violations of the plugin whose code ``package`` is in ``source_dir``.

    ``tests_dir`` adds its tests.
    """
    found = []
    if not (source_dir / "errors.py").is_file():
        found.append(f"{source_dir.name}/errors.py::errors-module")
    if tests_dir is not None:
        found.extend(_private_import_violations(tests_dir, package))
    return found


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
