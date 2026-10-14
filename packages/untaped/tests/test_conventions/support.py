"""Types shared by the convention-check tests."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from untaped.conventions.source import SourceFile
from untaped.conventions.structure import structure_class_violations, structure_source_violations
from untaped.plugins.registry import PluginSpec

#: Writes ``{relative path: source}`` under an importable directory and returns it.
Install = Callable[[dict[str, str]], Path]


def structure_violations(
    spec: PluginSpec,
    package: str,
    source_dir: Path,
    files: Sequence[SourceFile],
    *,
    tests_dir: Path | None = None,
) -> list[str]:
    """Structure's class and source checks together, as ``plugin_violations`` runs them."""
    return [
        *structure_class_violations(spec, package, source_dir),
        *structure_source_violations(spec, package, source_dir, files, tests_dir=tests_dir),
    ]
