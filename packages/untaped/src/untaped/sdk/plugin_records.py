"""The installed plugins as records: what ``untaped plugin list`` emits (``untaped.plugin``).

A ready plugin's record says where its code is (``package``, ``source_dir``)
and, from a checkout, where its tests are (``tests_dir``): ``tests/`` beside
the ``pyproject.toml`` that declares the plugin's entry point. An installed
wheel has no tests; the entry-point test keeps a walk up from
``site-packages`` from finding some unrelated project's ``tests/``.
"""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field

from untaped.runtime.registry import (
    PLUGINS_ENTRY_POINT_GROUP,
    CompositionResult,
    PluginCandidate,
    PluginSpec,
    candidate_distribution,
)
from untaped.sdk.records import AbsolutePath, Record

_UNKNOWN = "unknown"


class Plugin(Record, kind="untaped.plugin"):
    """One installed plugin: ready, or quarantined (and then nothing locates its code)."""

    table_columns: ClassVar[tuple[str, ...]] = ("name", "status", "distribution", "version")

    name: str
    status: Literal["ready", "quarantined"]
    distribution: str
    version: str
    package: str | None = Field(default=None, description="Its import package (`untaped_awx`).")
    source_dir: AbsolutePath | None = Field(
        default=None, description="Where that package's code is."
    )
    tests_dir: AbsolutePath | None = Field(
        default=None,
        description=(
            "`tests/` beside the `pyproject.toml` declaring its entry point, in a checkout."
        ),
    )


def plugin_records(
    result: CompositionResult, candidates: Sequence[PluginCandidate]
) -> list[Plugin]:
    """Every plugin of ``result``, ready or quarantined, by name then distribution."""
    # A distribution declares each entry-point name once, and a plugin whose
    # spec name differs from it is quarantined, so the reported (distribution,
    # name) finds the candidate of every row, an entry point that never resolved
    # and a blank distribution (reported as ``unknown``) included.
    by_key = {(candidate_distribution(item), item.name): item for item in candidates}
    rows: list[Plugin] = []
    for registered in result.plugins:
        distribution = registered.plugin_ref.distribution
        candidate = by_key.get((distribution, registered.spec.name))
        rows.append(_ready(registered.spec, distribution, candidate))
    for record in result.quarantine:
        candidate = by_key.get((record.distribution, record.name))
        version = (candidate.distribution_version if candidate else None) or _UNKNOWN
        rows.append(
            Plugin(
                name=record.name,
                status="quarantined",
                distribution=record.distribution,
                version=version,
            )
        )
    return sorted(rows, key=lambda row: (row.name, row.distribution))


def installed_plugins() -> list[Plugin]:
    """The plugins the current composition holds, as ``untaped plugin list`` shows them."""
    from untaped.runtime.bootstrap import composition  # noqa: PLC0415 - bootstrap is the root
    from untaped.runtime.registry import discover_candidates  # noqa: PLC0415

    return plugin_records(composition(), discover_candidates())


def _ready(spec: PluginSpec, distribution: str, candidate: PluginCandidate | None) -> Plugin:
    from untaped.conventions import package_of  # noqa: PLC0415 - imports the root

    version = (candidate.distribution_version if candidate else None) or _UNKNOWN
    name = spec.name
    package: str | None
    source_dir: Path | None
    try:
        package, source_dir = package_of(spec, None if candidate is None else candidate.target)
    except LookupError:
        package, source_dir = None, None
    tests_dir = None if source_dir is None else _tests_dir(source_dir, name)
    return Plugin(
        name=name,
        status="ready",
        distribution=distribution,
        version=version,
        package=package,
        source_dir=None if source_dir is None else source_dir.resolve(),
        tests_dir=tests_dir,
    )


def _tests_dir(source_dir: Path, name: str) -> Path | None:
    """``tests/`` beside the nearest ``pyproject.toml`` above ``source_dir`` declaring ``name``."""
    for folder in source_dir.resolve().parents:
        project = folder / "pyproject.toml"
        if not project.is_file():
            continue
        if name in _entry_points(project):
            tests = folder / "tests"
            return tests if tests.is_dir() else None
    return None


def _entry_points(project: Path) -> set[str]:
    """The ``untaped.plugins`` entry-point names ``project`` declares."""
    try:
        data = tomllib.loads(project.read_text(encoding="utf-8"))
    except OSError, UnicodeDecodeError, tomllib.TOMLDecodeError:
        return set()
    group = data.get("project", {}).get("entry-points", {}).get(PLUGINS_ENTRY_POINT_GROUP, {})
    return set(group) if isinstance(group, dict) else set()
