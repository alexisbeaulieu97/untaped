"""The installed plugins as records: what ``untaped plugin list`` emits (``untaped.plugin``).

A ready plugin's record says where its code is (``package``, ``source_dir``),
what it may import from (``requires``, ``fills``) and, from a checkout, where
its tests are (``tests_dir``): ``tests/`` beside
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
    RegisteredPlugin,
    candidate_distribution,
    declared_distributions,
)
from untaped.sdk.records import AbsolutePath, Record

_UNKNOWN = "unknown"


class Plugin(Record, kind="untaped.plugin"):
    """One installed plugin: ready, or quarantined (then only its entry point names its package)."""

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
    requires: tuple[str, ...] = Field(
        default=(),
        description=(
            "The distributions it requires, canonical names: what a default install pulls "
            "in, plus what an extra named after an owner it fills adds."
        ),
    )
    fills: tuple[str, ...] = Field(
        default=(), description="The owners whose contracts it fills (`workspace`)."
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
        rows.append(_ready(registered, candidate))
    for record in result.quarantine:
        candidate = by_key.get((record.distribution, record.name))
        version = (candidate.distribution_version if candidate else None) or _UNKNOWN
        rows.append(
            Plugin(
                name=record.name,
                status="quarantined",
                distribution=record.distribution,
                version=version,
                package=None if candidate is None else _entry_package(candidate),
            )
        )
    return sorted(rows, key=lambda row: (row.name, row.distribution))


def installed_plugins() -> list[Plugin]:
    """The plugins the current composition holds, as ``untaped plugin list`` shows them."""
    from untaped.runtime.bootstrap import composition  # noqa: PLC0415 - bootstrap is the root
    from untaped.runtime.registry import discover_candidates  # noqa: PLC0415

    return plugin_records(composition(), discover_candidates())


def _ready(registered: RegisteredPlugin, candidate: PluginCandidate | None) -> Plugin:
    from untaped.runtime.plugin_package import package_of  # noqa: PLC0415 - not at startup

    spec, distribution = registered.spec, registered.plugin_ref.distribution
    version = (candidate.distribution_version if candidate else None) or _UNKNOWN
    name = spec.name
    fills = frozenset(spec.provides)
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
        requires=tuple(sorted(declared_distributions(registered.plugin_ref.requires_dist, fills))),
        fills=tuple(sorted(fills)),
    )


def _entry_package(candidate: PluginCandidate) -> str | None:
    """The package a quarantined plugin's entry point names (``package:SPEC``)."""
    from untaped.runtime.plugin_package import candidate_package  # noqa: PLC0415 - not at startup

    return candidate_package(candidate.target)


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
