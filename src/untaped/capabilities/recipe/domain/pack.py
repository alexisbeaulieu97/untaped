"""Pack identity, the ``[tool.untaped_recipe]`` manifest, and qualified references.

One manifest model serves every uv project the capability reads: a recipe
pack, and a hooks-only project (a pack that exposes no recipes).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from packaging.utils import canonicalize_name
from pydantic import BaseModel, ConfigDict, field_validator

from untaped.capabilities.recipe.domain.hook_project import (
    dependency_name,
    hook_api_specifier,
    is_valid_dotted_name,
)
from untaped.capabilities.recipe.domain.paths import safe_library_name, safe_relative_path

PACK_PROJECT_PREFIX = "untaped-recipe-"
_PACK_PROJECT_BARE_NAME = PACK_PROJECT_PREFIX.removesuffix("-")


def pack_name_from_project(project_name: str) -> str:
    """Return the public pack name implied by a Python project name."""
    normalized = str(canonicalize_name(project_name))
    if not normalized or normalized == _PACK_PROJECT_BARE_NAME:
        raise ValueError("pack project name must include a pack name")
    if normalized.startswith(PACK_PROJECT_PREFIX):
        pack_name = normalized[len(PACK_PROJECT_PREFIX) :]
        if not pack_name:
            raise ValueError("pack project name must include a pack name")
        return pack_name
    return normalized


class RecipeEntry(BaseModel):
    """One recipe exposed by a pack manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("recipe path must not be empty")
        safe_relative_path(Path(value), field="recipe path")
        return value


class HookEntry(BaseModel):
    """One hook exposed by a pack manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    module: str = ""

    @field_validator("module")
    @classmethod
    def _module_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("module is required")
        if not is_valid_dotted_name(value):
            raise ValueError(f"invalid module name: {value}")
        return value


class PackManifest(BaseModel):
    """Parsed ``[project]`` and ``[tool.untaped_recipe]`` metadata of a uv project."""

    model_config = ConfigDict(frozen=True)

    name: str
    project_name: str
    version: str
    requires_hook_api: str | None = None
    recipes: dict[str, RecipeEntry]
    hooks: dict[str, HookEntry]
    runtime_dependencies: tuple[str, ...] = ()

    @field_validator("recipes")
    @classmethod
    def _recipe_ids(cls, value: dict[str, RecipeEntry]) -> dict[str, RecipeEntry]:
        for recipe_id in value:
            safe_library_name(recipe_id, field="recipe")
        return value

    @field_validator("hooks")
    @classmethod
    def _hook_names(cls, value: dict[str, HookEntry]) -> dict[str, HookEntry]:
        for name in value:
            if not is_valid_dotted_name(name):
                raise ValueError(f"invalid hook name: {name}")
        return value

    @classmethod
    def from_pyproject(
        cls,
        data: Mapping[str, object],
        *,
        source: Path,
        require_pack: bool = True,
    ) -> PackManifest:
        """Build a manifest from parsed pyproject ``data`` read from ``source``.

        ``require_pack`` demands ``[project].name`` and ``[tool.untaped_recipe]``;
        without it (a local hook project) both may be absent and the manifest
        is simply empty.
        """
        project = _table(data, "project")
        if project is None and require_pack:
            raise ValueError(f"pack project pyproject missing [project]: {source}")
        project = project or {}
        raw_name = project.get("name")
        if raw_name is None and not require_pack:
            raw_name = ""
        if not isinstance(raw_name, str):
            raise ValueError("[project].name must be a string")
        raw_version = project.get("version", "0")
        if not isinstance(raw_version, str):
            raise ValueError("[project].version must be a string")

        tool_config = _table(data, "tool", "untaped_recipe")
        if tool_config is None and require_pack:
            raise ValueError(f"pack project pyproject missing [tool.untaped_recipe]: {source}")
        tool_config = tool_config or {}
        return cls(
            name=pack_name_from_project(raw_name) if require_pack else _hook_project_name(raw_name),
            project_name=raw_name,
            version=raw_version,
            requires_hook_api=_requires_hook_api(tool_config),
            recipes=dict(_table(tool_config, "recipes", parent="tool.untaped_recipe") or {}),
            hooks=dict(_table(tool_config, "hooks", parent="tool.untaped_recipe") or {}),
            runtime_dependencies=_runtime_dependencies(project),
        )


@dataclass(frozen=True)
class InstalledPack:
    """One installed pack plus library bookkeeping."""

    name: str
    root: Path
    manifest: PackManifest
    source: str
    rev: str
    installed_version: str

    @classmethod
    def local(cls, path: Path, manifest: PackManifest) -> InstalledPack:
        """Wrap an explicit-path pack that is not tracked by the library index."""
        return cls(
            name=manifest.name,
            root=path,
            manifest=manifest,
            source=str(path),
            rev="",
            installed_version=manifest.version,
        )


@dataclass(frozen=True)
class PackRef:
    """A bare or pack-qualified recipe/hook reference."""

    pack: str | None
    name: str


def parse_ref(text: str) -> PackRef:
    """Parse ``name`` or ``pack/name`` into a structured reference."""
    parts = text.split("/")
    if len(parts) == 1:
        name = parts[0]
        if not name or name == "..":
            raise ValueError("qualified refs must use <pack>/<name>")
        return PackRef(pack=None, name=name)
    if len(parts) != 2:
        raise ValueError("qualified refs must use <pack>/<name>")
    pack, name = parts
    if not pack or not name or pack == ".." or name == "..":
        raise ValueError("qualified refs must use <pack>/<name>")
    return PackRef(pack=pack, name=name)


def _table(data: Mapping[str, object], *path: str, parent: str = "") -> Mapping[str, object] | None:
    """Return the nested TOML table at ``path``: ``None`` when absent, an error when not a table."""
    current: Mapping[str, object] = data
    for depth, key in enumerate(path):
        value = current.get(key)
        if value is None:
            return None
        if not isinstance(value, Mapping):
            field = ".".join(filter(None, (parent, *path[: depth + 1])))
            raise ValueError(f"[{field}] must be a table")
        current = value
    return current


def _hook_project_name(project_name: str) -> str:
    """Best-effort pack name for a hooks-only project, which need not name a pack."""
    try:
        return pack_name_from_project(project_name)
    except ValueError:
        return ""


def _requires_hook_api(tool_config: Mapping[str, object]) -> str | None:
    raw = tool_config.get("requires_hook_api")
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("[tool.untaped_recipe].requires_hook_api must be a string")
    requirement = raw.strip()
    if not requirement:
        raise ValueError("[tool.untaped_recipe].requires_hook_api must not be empty")
    hook_api_specifier(requirement)
    return requirement


def _runtime_dependencies(project: Mapping[str, object]) -> tuple[str, ...]:
    raw_dependencies = project.get("dependencies")
    if raw_dependencies is None:
        return ()
    if not isinstance(raw_dependencies, list):
        raise ValueError("[project].dependencies must be an array")
    for dependency in raw_dependencies:
        if not isinstance(dependency, str):
            raise ValueError("[project].dependencies entries must be strings")
        dependency_name(dependency)
    return tuple(raw_dependencies)
