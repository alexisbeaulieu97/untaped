"""Plugin registration, discovery, and diagnostics runtime."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import Protocol

import yaml
from cyclopts import App
from pydantic import BaseModel

from untaped.errors import ConfigError
from untaped.settings import (
    BUILTIN_STATE_SECTIONS,
    register_profile_settings,
    register_state_settings,
    validate_disjoint_settings_sections,
)
from untaped.ui import BUILTIN_THEMES, ThemeSpec

ENTRY_POINT_GROUP = "untaped.plugins"
_SUPPORTED_PLUGIN_API_VERSION = 2


class UntapedPlugin(Protocol):
    """Object exposed by plugin packages through the ``untaped.plugins`` entry point."""

    id: str
    untaped_api_version: int

    def register(self, registry: PluginRegistry) -> None: ...


@dataclass(frozen=True)
class DiagnosticResult:
    """One plugin diagnostic outcome."""

    name: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class PluginLoadError:
    """A plugin entry point that failed to load or register."""

    name: str
    error: str


@dataclass(frozen=True)
class SkillSpec:
    """A packaged agent skill contributed by core or a plugin."""

    name: str
    source: Path
    description: str


class PluginRegistry:
    """In-process registry populated by installed plugins."""

    def __init__(self, *, reserved_cli_names: Iterable[str] = ()) -> None:
        self.reserved_cli_names = set(reserved_cli_names)
        self.plugin_ids: set[str] = set()
        self.clis: dict[str, App] = {}
        self.profile_sections: dict[str, type[BaseModel]] = {}
        self.state_sections: dict[str, type[BaseModel]] = {}
        self.themes: dict[str, ThemeSpec] = {}
        self.skills: dict[str, SkillSpec] = {}
        self.diagnostics: dict[str, Callable[[], DiagnosticResult]] = {}
        self.load_errors: list[PluginLoadError] = []

    def add_plugin_id(self, plugin_id: str) -> None:
        if plugin_id in self.plugin_ids:
            raise ConfigError(f"duplicate plugin id: {plugin_id}")
        self.plugin_ids.add(plugin_id)

    def add_cli(self, name: str, app: App) -> None:
        if name in self.reserved_cli_names:
            raise ConfigError(f"reserved CLI command: {name}")
        if name in self.clis:
            raise ConfigError(f"duplicate CLI command: {name}")
        self.clis[name] = app

    def add_profile_settings(self, section: str, model: type[BaseModel]) -> None:
        if section in BUILTIN_STATE_SECTIONS:
            raise ConfigError(f"reserved profile settings section: {section}")
        if section in self.profile_sections:
            raise ConfigError(f"duplicate profile settings section: {section}")
        state_model = self.state_sections.get(section)
        if state_model is not None:
            validate_disjoint_settings_sections(section, model, state_model)
        self.profile_sections[section] = model

    def add_state_settings(self, section: str, model: type[BaseModel]) -> None:
        if section in BUILTIN_STATE_SECTIONS:
            raise ConfigError(f"reserved state settings section: {section}")
        if section in self.state_sections:
            raise ConfigError(f"duplicate state settings section: {section}")
        profile_model = self.profile_sections.get(section)
        if profile_model is not None:
            validate_disjoint_settings_sections(section, profile_model, model)
        self.state_sections[section] = model

    def add_theme(self, name: str, spec: ThemeSpec) -> None:
        if name in BUILTIN_THEMES:
            raise ConfigError(f"reserved theme: {name}")
        if name in self.themes:
            raise ConfigError(f"duplicate theme: {name}")
        self.themes[name] = spec

    def add_skill(self, spec: SkillSpec) -> None:
        name = spec.name.strip()
        if name != "untaped" and not name.startswith("untaped-"):
            raise ConfigError("skill name must be 'untaped' or start with 'untaped-'")
        if name in self.skills:
            raise ConfigError(f"duplicate skill: {name}")
        source = Path(spec.source)
        if not source.is_dir():
            raise ConfigError(f"skill source directory does not exist: {source}")
        skill_md = source / "SKILL.md"
        if not skill_md.is_file():
            raise ConfigError(f"skill source must contain SKILL.md: {source}")
        frontmatter = _read_skill_frontmatter(skill_md)
        if frontmatter.get("name") != name:
            raise ConfigError(f"SKILL.md name must match skill name: {name}")
        description = frontmatter.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ConfigError(f"SKILL.md description is required: {skill_md}")
        declared_description = spec.description.strip()
        if not declared_description:
            raise ConfigError(f"skill description is required: {name}")
        self.skills[name] = SkillSpec(
            name=name,
            source=source,
            description=declared_description,
        )

    def add_diagnostic(self, name: str, check: Callable[[], DiagnosticResult]) -> None:
        if name in self.diagnostics:
            raise ConfigError(f"duplicate diagnostic: {name}")
        self.diagnostics[name] = check

    def record_load_error(self, name: str, exc: BaseException) -> None:
        self.load_errors.append(PluginLoadError(name=name, error=str(exc)))

    def run_diagnostics(self) -> list[DiagnosticResult]:
        return [check() for check in self.diagnostics.values()]

    def apply_config_sections(self) -> None:
        """Publish successfully registered config sections to the settings registry."""
        for section, model in self.profile_sections.items():
            register_profile_settings(section, model)
        for section, model in self.state_sections.items():
            register_state_settings(section, model)


_CURRENT_REGISTRY = PluginRegistry()


def current_registry() -> PluginRegistry:
    """Return the registry used by ``untaped plugins`` commands."""
    return _CURRENT_REGISTRY


def set_current_registry(registry: PluginRegistry) -> None:
    """Set the registry used by ``untaped plugins`` commands."""
    global _CURRENT_REGISTRY
    _CURRENT_REGISTRY = registry


def discover_plugins(registry: PluginRegistry | None = None) -> list[UntapedPlugin]:
    """Load plugin objects from installed Python entry points."""
    plugins: list[UntapedPlugin] = []
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            plugins.append(ep.load())
        except Exception as exc:
            if registry is None:
                raise
            registry.record_load_error(ep.name, exc)
    return plugins


def register_plugins(registry: PluginRegistry, plugins: Iterable[UntapedPlugin]) -> PluginRegistry:
    """Register plugins, recording failures instead of poisoning the CLI."""
    for plugin in plugins:
        plugin_id = getattr(plugin, "id", plugin.__class__.__module__)
        clis = dict(registry.clis)
        plugin_ids = set(registry.plugin_ids)
        profile_sections = dict(registry.profile_sections)
        state_sections = dict(registry.state_sections)
        themes = dict(registry.themes)
        skills = dict(registry.skills)
        diagnostics = dict(registry.diagnostics)
        try:
            _validate_plugin_api_version(plugin, plugin_id)
            registry.add_plugin_id(plugin_id)
            plugin.register(registry)
        except Exception as exc:
            registry.clis = clis
            registry.plugin_ids = plugin_ids
            registry.profile_sections = profile_sections
            registry.state_sections = state_sections
            registry.themes = themes
            registry.skills = skills
            registry.diagnostics = diagnostics
            registry.record_load_error(plugin_id, exc)
    registry.apply_config_sections()
    return registry


def _validate_plugin_api_version(plugin: UntapedPlugin, plugin_id: str) -> None:
    sentinel = object()
    api_version = getattr(plugin, "untaped_api_version", sentinel)
    if api_version is sentinel:
        raise ConfigError(
            f"plugin {plugin_id!r} is missing required untaped_api_version; "
            f"supported version is {_SUPPORTED_PLUGIN_API_VERSION}"
        )
    if type(api_version) is not int:
        raise ConfigError(
            f"plugin {plugin_id!r} has invalid untaped_api_version: expected int "
            f"{_SUPPORTED_PLUGIN_API_VERSION}, got {api_version!r}"
        )
    if api_version != _SUPPORTED_PLUGIN_API_VERSION:
        raise ConfigError(
            f"plugin {plugin_id!r} declares unsupported untaped_api_version "
            f"{api_version}; supported version is {_SUPPORTED_PLUGIN_API_VERSION}"
        )


def _read_skill_frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text()
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ConfigError(f"SKILL.md must start with YAML frontmatter: {path}")
    close_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line == "---"),
        None,
    )
    if close_index is None:
        raise ConfigError(f"SKILL.md frontmatter is not closed: {path}")
    raw_frontmatter = "\n".join(lines[1:close_index])
    loaded = yaml.safe_load(raw_frontmatter)
    if not isinstance(loaded, dict):
        raise ConfigError(f"SKILL.md frontmatter must be a mapping: {path}")
    return loaded
