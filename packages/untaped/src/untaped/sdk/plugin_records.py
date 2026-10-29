"""The installed plugins as records (``untaped.installed_plugin``): ``untaped plugin list``.

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
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

from untaped.runtime.registry import (
    PLUGINS_ENTRY_POINT_GROUP,
    CompositionResult,
    PluginCandidate,
    candidate_distribution,
    declared_distributions,
)
from untaped.sdk.fields import Column, Status
from untaped.sdk.records import AbsolutePath, Record

if TYPE_CHECKING:
    from untaped.runtime.composed import ComposedPlugin

_UNKNOWN = "unknown"


class InstalledPlugin(Record):
    """One installed plugin: ready, or quarantined (then only its entry point names its package)."""

    name: Annotated[str, Column]
    status: Annotated[Literal["ready", "quarantined"], Status(success=("ready",)), Column]
    distribution: Annotated[str, Column]
    version: Annotated[str, Column]
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
    root: bool = Field(
        default=False,
        description=(
            "Its commands mount at the root: `untaped` itself. Filter on it for the plugins "
            "installed into untaped."
        ),
    )


def plugin_records(
    result: CompositionResult, candidates: Sequence[PluginCandidate]
) -> list[InstalledPlugin]:
    """Every plugin of ``result``, ready or quarantined, by name then distribution.

    The root plugin (core, ``untaped`` itself) has its row like any plugin's,
    marked ``root``.
    """
    # A distribution declares each entry-point name once, and a plugin whose
    # name differs from it is quarantined, so the reported (distribution,
    # name) finds the candidate of every row, an entry point that never resolved
    # and a blank distribution (reported as ``unknown``) included.
    by_key = {(candidate_distribution(item), item.name): item for item in candidates}
    rows: list[InstalledPlugin] = []
    for plugin in result.plugins:
        distribution = plugin.plugin_ref.distribution
        candidate = by_key.get((distribution, plugin.name))
        rows.append(_ready(plugin, candidate))
    for record in result.quarantine:
        candidate = by_key.get((record.distribution, record.name))
        version = (candidate.distribution_version if candidate else None) or _UNKNOWN
        rows.append(
            InstalledPlugin(
                name=record.name,
                status="quarantined",
                distribution=record.distribution,
                version=version,
                package=None if candidate is None else _entry_package(candidate),
            )
        )
    return sorted(rows, key=lambda row: (row.name, row.distribution))


def installed_plugins() -> list[InstalledPlugin]:
    """The plugins the current composition holds, as ``untaped plugin list`` shows them.

    Each record reads the candidate it was composed from, so a composition
    of given candidates (a test's) lists them as it holds them.
    """
    from untaped.runtime.bootstrap import composition  # noqa: PLC0415 - bootstrap is the root

    result = composition()
    return plugin_records(result, result.candidates)


def _ready(plugin: ComposedPlugin, candidate: PluginCandidate | None) -> InstalledPlugin:
    from untaped.runtime.plugin_package import package_of  # noqa: PLC0415 - not at startup

    distribution = plugin.plugin_ref.distribution
    # no candidate (the root's): its distribution's version, read now, never while composing
    found = candidate.distribution_version if candidate else _installed_version(distribution)
    version = found or _UNKNOWN
    name = plugin.name
    fills = frozenset(plugin.fill_owners)
    package: str | None
    source_dir: Path | None
    try:
        package, source_dir = package_of(plugin)
    except LookupError:
        package, source_dir = None, None
    tests_dir = None if source_dir is None else _tests_dir(source_dir, name)
    return InstalledPlugin(
        name=name,
        status="ready",
        distribution=distribution,
        version=version,
        package=package,
        source_dir=None if source_dir is None else source_dir.resolve(),
        tests_dir=tests_dir,
        requires=tuple(sorted(declared_distributions(plugin.plugin_ref.requires_dist, fills))),
        fills=tuple(sorted(fills)),
        root=plugin.root,
    )


def _installed_version(distribution: str) -> str | None:
    from importlib import metadata  # noqa: PLC0415 - not at startup

    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def _entry_package(candidate: PluginCandidate) -> str | None:
    """The package a quarantined plugin's entry point names (``package.plugin:Class``)."""
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
