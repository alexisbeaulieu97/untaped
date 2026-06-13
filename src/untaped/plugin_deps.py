"""Discovery of inter-plugin dependencies declared by local plugin checkouts.

Managed sync resolves recorded specs with ``--no-sources`` so one plugin's
dev-machine ``[tool.uv.sources]`` paths never leak into the managed
environment. The flip side: a local plugin depending on another local plugin
fails resolution unless that dependency is recorded as its own explicit spec.
These helpers inspect a local checkout's ``pyproject.toml`` and translate
plugin-to-plugin source entries into explicit, user-visible install specs.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from untaped.errors import ConfigError
from untaped.plugin_specs import normalize_package_name
from untaped.settings import PluginInstallSpec

_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_PLUGIN_PREFIX = "untaped-"


@dataclass(frozen=True)
class PluginDepSource:
    """A ``[tool.uv.sources]`` entry usable for an explicit recorded spec."""

    kind: Literal["path", "git"]
    target: str
    rev: str | None = None
    branch: str | None = None
    tag: str | None = None
    editable: bool = False


@dataclass(frozen=True)
class PluginDependency:
    """One untaped-plugin dependency declared by a local plugin project."""

    name: str
    requirement: str
    source: PluginDepSource | None


def local_plugin_dependencies(project_dir: Path) -> list[PluginDependency]:
    """Return the untaped-plugin dependencies declared by a local project."""
    pyproject = project_dir / "pyproject.toml"
    if not pyproject.is_file():
        return []
    try:
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"could not parse plugin pyproject {pyproject}: {exc}") from exc
    project = data.get("project")
    if not isinstance(project, dict):
        return []
    requirements = project.get("dependencies")
    if not isinstance(requirements, list):
        return []
    sources = _uv_sources(data)
    dependencies: list[PluginDependency] = []
    for requirement in requirements:
        if not isinstance(requirement, str):
            continue
        name = _requirement_name(requirement)
        if name is None or not name.startswith(_PLUGIN_PREFIX):
            continue
        dependencies.append(
            PluginDependency(
                name=name,
                requirement=requirement.strip(),
                source=sources.get(name),
            )
        )
    return dependencies


def dependency_install_spec(
    dependency: PluginDependency,
    *,
    base_dir: Path,
) -> PluginInstallSpec | None:
    """Translate a plugin dependency into an explicit recorded install spec.

    Returns ``None`` for index-resolvable dependencies (no source entry):
    ``--no-sources`` resolution already handles those.
    """
    source = dependency.source
    if source is None:
        return None
    if source.kind == "path":
        path = (base_dir / Path(source.target).expanduser()).resolve()
        if not path.exists():
            raise ConfigError(
                f"plugin dependency {dependency.name} points at a missing path: "
                f"{source.target} (relative to {base_dir})"
            )
        return PluginInstallSpec(
            spec=str(path),
            editable=source.editable,
            name=dependency.name,
        )
    reference = f"{dependency.name} @ git+{source.target}"
    ref = source.rev or source.tag or source.branch
    if ref:
        reference += f"@{ref}"
    return PluginInstallSpec(spec=reference, editable=False, name=dependency.name)


def expand_plugin_dependencies(
    specs: list[PluginInstallSpec],
    *,
    already_recorded: set[str],
) -> list[tuple[PluginInstallSpec, str]]:
    """Walk local specs and return (auto spec, required-by name) pairs.

    Only local directory specs are inspected: for git/index specs the
    author's source entries describe *their* machine and cannot be trusted.
    Path-sourced dependencies are walked recursively; a visited set keyed by
    normalized package name terminates cycles.
    """
    seen = set(already_recorded)
    for spec in specs:
        if spec.name:
            seen.add(normalize_package_name(spec.name))
    queue = [spec for spec in specs if _local_project_dir(spec) is not None]
    expanded: list[tuple[PluginInstallSpec, str]] = []
    while queue:
        parent = queue.pop(0)
        parent_dir = _local_project_dir(parent)
        if parent_dir is None:
            continue
        parent_name = parent.name or parent.spec
        for dependency in local_plugin_dependencies(parent_dir):
            if dependency.name in seen:
                continue
            auto = dependency_install_spec(dependency, base_dir=parent_dir)
            if auto is None:
                continue
            seen.add(dependency.name)
            expanded.append((auto, parent_name))
            queue.append(auto)
    return expanded


def _local_project_dir(spec: PluginInstallSpec) -> Path | None:
    path = Path(spec.spec)
    if path.is_absolute() and path.is_dir():
        return path
    return None


def _requirement_name(requirement: str) -> str | None:
    match = _REQUIREMENT_NAME.match(requirement)
    if match is None:
        return None
    return normalize_package_name(match.group(1))


def _uv_sources(data: dict[str, object]) -> dict[str, PluginDepSource]:
    tool = data.get("tool")
    if not isinstance(tool, dict):
        return {}
    uv = tool.get("uv")
    if not isinstance(uv, dict):
        return {}
    raw_sources = uv.get("sources")
    if not isinstance(raw_sources, dict):
        return {}
    sources: dict[str, PluginDepSource] = {}
    for raw_name, entry in raw_sources.items():
        if not isinstance(raw_name, str) or not isinstance(entry, dict):
            continue
        source = _parse_source(entry)
        if source is not None:
            sources[normalize_package_name(raw_name)] = source
    return sources


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _parse_source(entry: dict[str, object]) -> PluginDepSource | None:
    path = entry.get("path")
    if isinstance(path, str):
        return PluginDepSource(
            kind="path",
            target=path,
            editable=bool(entry.get("editable", False)),
        )
    git = entry.get("git")
    if isinstance(git, str):
        return PluginDepSource(
            kind="git",
            target=git,
            rev=_optional_str(entry.get("rev")),
            branch=_optional_str(entry.get("branch")),
            tag=_optional_str(entry.get("tag")),
        )
    # workspace/index/url sources have no managed-spec translation; the
    # dependency falls back to index resolution under --no-sources.
    return None
