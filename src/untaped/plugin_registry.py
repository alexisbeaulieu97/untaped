"""Plugin registration, discovery, and diagnostics runtime."""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import import_module
from importlib.metadata import entry_points
from pathlib import Path
from typing import Protocol, cast

import yaml
from cyclopts import App
from pydantic import BaseModel

from untaped.errors import ConfigError
from untaped.settings import (
    BUILTIN_STATE_SECTIONS,
    register_profile_settings,
    register_settings_layout,
    register_state_settings,
    validate_disjoint_settings_sections,
)
from untaped.settings_layout import SettingsLayout
from untaped.ui import BUILTIN_THEMES, ThemeSpec

ENTRY_POINT_GROUP = "untaped.plugins"
_LEGACY_PLUGIN_API_VERSION = 2
_MANIFEST_PLUGIN_API_VERSION = 3
_ROOT_OPTION_PLUGIN_API_VERSION = 4
_SUPPORTED_PLUGIN_API_VERSIONS = frozenset(
    {
        _LEGACY_PLUGIN_API_VERSION,
        _MANIFEST_PLUGIN_API_VERSION,
        _ROOT_OPTION_PLUGIN_API_VERSION,
    }
)


def _validate_import_path(import_path: str, owner: str) -> None:
    module, sep, attribute = import_path.partition(":")
    if not sep or not module or not attribute:
        raise ConfigError(f"{owner} import path must be 'module:attribute', got {import_path!r}")


class UntapedPlugin(Protocol):
    """Object exposed by plugin packages through the ``untaped.plugins`` entry point.

    Version 3 plugins provide ``manifest() -> PluginManifest``; version 2
    plugins provide ``register(registry) -> None``. Both shapes are accepted;
    the declared ``untaped_api_version`` selects the contract.
    """

    id: str
    untaped_api_version: int


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


@dataclass(frozen=True)
class CliSpec:
    """One root CLI command contributed by a plugin manifest.

    Exactly one of ``app`` (an already-built Cyclopts app) or ``import_path``
    (``"module.path:attribute"``, resolved only when the command is actually
    dispatched) must be set. ``import_path`` keeps plugin CLI modules off the
    startup import path; ``help`` is the one-line summary shown in root
    ``--help`` before the real app is imported.
    """

    name: str
    app: App | None = None
    import_path: str | None = None
    help: str = ""

    def __post_init__(self) -> None:
        if (self.app is None) == (self.import_path is None):
            raise ConfigError(f"CLI spec {self.name!r} must set exactly one of app or import_path")
        if self.import_path is not None:
            module, sep, attribute = self.import_path.partition(":")
            if not sep or not module or not attribute:
                raise ConfigError(
                    f"CLI spec {self.name!r} import_path must be 'module:attribute', "
                    f"got {self.import_path!r}"
                )


@dataclass(frozen=True)
class RootOptionSpec:
    """A value-taking root-level option contributed by a plugin (e.g. ``--profile``).

    The handler behind ``handler_import_path`` (``Callable[[str], None]``) is
    imported only when the option is actually used, and runs before the
    dispatched command body reads settings.
    """

    name: str
    help: str
    handler_import_path: str

    def __post_init__(self) -> None:
        if not self.name.startswith("--") or len(self.name) <= 2:
            raise ConfigError(f"root option name must look like '--option', got {self.name!r}")
        _validate_import_path(self.handler_import_path, f"root option {self.name!r} handler")


@dataclass(frozen=True)
class SettingsLayoutSpec:
    """How raw config maps to effective settings; at most one across all plugins.

    ``import_path`` resolves lazily to a ``untaped.settings_layout.SettingsLayout``
    instance the first time settings are read.
    """

    import_path: str

    def __post_init__(self) -> None:
        _validate_import_path(self.import_path, "settings layout")


@dataclass(frozen=True)
class PluginManifest:
    """Everything a version-3/4 plugin contributes, as data.

    Core validates the whole manifest against the registry and commits it
    atomically: a manifest that conflicts with already-registered plugins (or
    with itself) registers nothing and is reported through
    ``untaped plugins doctor``. ``root_options`` and ``settings_layout``
    require ``untaped_api_version`` 4.
    """

    clis: Sequence[CliSpec] = ()
    profile_settings: Mapping[str, type[BaseModel]] = field(default_factory=dict)
    state_settings: Mapping[str, type[BaseModel]] = field(default_factory=dict)
    themes: Mapping[str, ThemeSpec] = field(default_factory=dict)
    skills: Sequence[SkillSpec] = ()
    diagnostics: Mapping[str, Callable[[], DiagnosticResult]] = field(default_factory=dict)
    root_options: Sequence[RootOptionSpec] = ()
    settings_layout: SettingsLayoutSpec | None = None


class PluginRegistry:
    """In-process registry populated by installed plugins."""

    def __init__(self, *, reserved_cli_names: Iterable[str] = ()) -> None:
        self.reserved_cli_names = set(reserved_cli_names)
        self.plugin_ids: set[str] = set()
        self.clis: dict[str, App] = {}
        self.lazy_clis: dict[str, CliSpec] = {}
        self.profile_sections: dict[str, type[BaseModel]] = {}
        self.state_sections: dict[str, type[BaseModel]] = {}
        self.themes: dict[str, ThemeSpec] = {}
        self.skills: dict[str, SkillSpec] = {}
        self.diagnostics: dict[str, Callable[[], DiagnosticResult]] = {}
        self.root_options: dict[str, RootOptionSpec] = {}
        self.settings_layout: SettingsLayoutSpec | None = None
        self.load_errors: list[PluginLoadError] = []

    def add_plugin_id(self, plugin_id: str) -> None:
        if plugin_id in self.plugin_ids:
            raise ConfigError(f"duplicate plugin id: {plugin_id}")
        self.plugin_ids.add(plugin_id)

    def add_cli(self, name: str, app: App) -> None:
        self._validate_cli_name(name)
        self.clis[name] = app

    def add_lazy_cli(self, spec: CliSpec) -> None:
        if spec.import_path is None:
            raise ConfigError(f"CLI spec {spec.name!r} has no import_path; use add_cli")
        self._validate_cli_name(spec.name)
        self.lazy_clis[spec.name] = spec

    def _validate_cli_name(self, name: str) -> None:
        if name in self.reserved_cli_names:
            raise ConfigError(f"reserved CLI command: {name}")
        if name in self.clis or name in self.lazy_clis:
            raise ConfigError(f"duplicate CLI command: {name}")

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

    def add_root_option(self, spec: RootOptionSpec) -> None:
        if spec.name in self.root_options:
            raise ConfigError(f"duplicate root option: {spec.name}")
        self.root_options[spec.name] = spec

    def set_settings_layout(self, spec: SettingsLayoutSpec) -> None:
        if self.settings_layout is not None:
            raise ConfigError(
                f"a settings layout is already registered ({self.settings_layout.import_path}); "
                f"only one plugin may contribute one"
            )
        self.settings_layout = spec

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
        if self.settings_layout is not None:
            spec = self.settings_layout
            register_settings_layout(
                lambda: resolve_settings_layout(spec),
                key=spec.import_path,
            )

    # Every per-plugin registration collection; _staging_copy/_adopt operate on
    # this list so a new collection cannot be forgotten in one of the two.
    _STATE_FIELDS = (
        "plugin_ids",
        "clis",
        "lazy_clis",
        "profile_sections",
        "state_sections",
        "themes",
        "skills",
        "diagnostics",
        "root_options",
        "settings_layout",
    )

    def _staging_copy(self) -> PluginRegistry:
        """Copy registration state so one plugin's contributions commit atomically.

        ``load_errors`` is intentionally shared (same list object): error
        recording must survive a discarded staging copy.
        """
        staged = PluginRegistry(reserved_cli_names=self.reserved_cli_names)
        for field_name in self._STATE_FIELDS:
            setattr(staged, field_name, copy.copy(getattr(self, field_name)))
        staged.load_errors = self.load_errors
        return staged

    def _adopt(self, staged: PluginRegistry) -> None:
        """Take over a staging copy's state after a successful registration."""
        for field_name in self._STATE_FIELDS:
            setattr(self, field_name, getattr(staged, field_name))


_CURRENT_REGISTRY = PluginRegistry()


def current_registry() -> PluginRegistry:
    """Return the registry used by ``untaped plugins`` commands."""
    return _CURRENT_REGISTRY


def set_current_registry(registry: PluginRegistry) -> None:
    """Set the registry used by ``untaped plugins`` commands."""
    global _CURRENT_REGISTRY
    _CURRENT_REGISTRY = registry


def resolve_lazy_cli(spec: CliSpec) -> App:
    """Import and return the Cyclopts app behind a CLI spec."""
    if spec.app is not None:
        return spec.app
    import_path = spec.import_path
    if import_path is None:  # pragma: no cover - CliSpec.__post_init__ forbids this
        raise ConfigError(f"CLI spec {spec.name!r} has neither app nor import_path")
    module_name, _, attribute = import_path.partition(":")
    try:
        module = import_module(module_name)
    except Exception as exc:
        raise ConfigError(
            f"plugin command {spec.name!r} failed to import {module_name!r}: {exc}"
        ) from exc
    app = getattr(module, attribute, None)
    if not isinstance(app, App):
        raise ConfigError(
            f"plugin command {spec.name!r}: {import_path!r} does not resolve to a cyclopts App"
        )
    return app


def resolve_settings_layout(spec: SettingsLayoutSpec) -> SettingsLayout:
    """Import and return the layout instance behind a settings layout spec."""
    module_name, _, attribute = spec.import_path.partition(":")
    try:
        module = import_module(module_name)
    except Exception as exc:
        raise ConfigError(f"settings layout failed to import {module_name!r}: {exc}") from exc
    layout = getattr(module, attribute, None)
    if not isinstance(layout, SettingsLayout):
        raise ConfigError(
            f"settings layout {spec.import_path!r} does not resolve to a SettingsLayout"
        )
    return layout


def resolve_root_option_handler(spec: RootOptionSpec) -> Callable[[str], None]:
    """Import and return the handler behind a root option spec."""
    module_name, _, attribute = spec.handler_import_path.partition(":")
    try:
        module = import_module(module_name)
    except Exception as exc:
        raise ConfigError(
            f"root option {spec.name!r} failed to import {module_name!r}: {exc}"
        ) from exc
    handler = getattr(module, attribute, None)
    if not callable(handler):
        raise ConfigError(
            f"root option {spec.name!r}: {spec.handler_import_path!r} "
            f"does not resolve to a callable"
        )
    return cast("Callable[[str], None]", handler)


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
    """Register plugins, recording failures instead of poisoning the CLI.

    Each plugin registers against a staging copy of the registry; the copy is
    adopted only when the whole registration succeeds, so a failing plugin
    contributes nothing regardless of how far it got.
    """
    for plugin in plugins:
        plugin_id = getattr(plugin, "id", plugin.__class__.__module__)
        staged = registry._staging_copy()
        try:
            api_version = _validate_plugin_api_version(plugin, plugin_id)
            staged.add_plugin_id(plugin_id)
            if api_version >= _MANIFEST_PLUGIN_API_VERSION:
                manifest = _plugin_manifest(plugin, plugin_id)
                _validate_manifest_version(manifest, plugin_id, api_version)
                _apply_manifest(staged, manifest)
            else:
                _legacy_register(plugin, plugin_id, staged)
        except Exception as exc:
            registry.record_load_error(plugin_id, exc)
            continue
        registry._adopt(staged)
    registry.apply_config_sections()
    return registry


def _validate_manifest_version(manifest: PluginManifest, plugin_id: str, api_version: int) -> None:
    if api_version >= _ROOT_OPTION_PLUGIN_API_VERSION:
        return
    if manifest.root_options or manifest.settings_layout is not None:
        raise ConfigError(
            f"plugin {plugin_id!r} declares root_options or settings_layout, "
            f"which require untaped_api_version {_ROOT_OPTION_PLUGIN_API_VERSION}"
        )


def _plugin_manifest(plugin: UntapedPlugin, plugin_id: str) -> PluginManifest:
    manifest_method = getattr(plugin, "manifest", None)
    if not callable(manifest_method):
        raise ConfigError(
            f"plugin {plugin_id!r} declares untaped_api_version "
            f"{_MANIFEST_PLUGIN_API_VERSION} or later but does not provide a manifest() method"
        )
    manifest = manifest_method()
    if not isinstance(manifest, PluginManifest):
        raise ConfigError(
            f"plugin {plugin_id!r} manifest() must return a PluginManifest, "
            f"got {type(manifest).__name__}"
        )
    return manifest


def _apply_manifest(staged: PluginRegistry, manifest: PluginManifest) -> None:
    for spec in manifest.clis:
        if spec.app is not None:
            staged.add_cli(spec.name, spec.app)
        else:
            staged.add_lazy_cli(spec)
    for section, model in manifest.profile_settings.items():
        staged.add_profile_settings(section, model)
    for section, model in manifest.state_settings.items():
        staged.add_state_settings(section, model)
    for name, theme in manifest.themes.items():
        staged.add_theme(name, theme)
    for skill in manifest.skills:
        staged.add_skill(skill)
    for name, check in manifest.diagnostics.items():
        staged.add_diagnostic(name, check)
    for option in manifest.root_options:
        staged.add_root_option(option)
    if manifest.settings_layout is not None:
        staged.set_settings_layout(manifest.settings_layout)


def _legacy_register(plugin: UntapedPlugin, plugin_id: str, staged: PluginRegistry) -> None:
    register = getattr(plugin, "register", None)
    if not callable(register):
        raise ConfigError(
            f"plugin {plugin_id!r} declares untaped_api_version "
            f"{_LEGACY_PLUGIN_API_VERSION} but does not provide a register() method"
        )
    register(staged)


def _validate_plugin_api_version(plugin: UntapedPlugin, plugin_id: str) -> int:
    supported = ", ".join(str(v) for v in sorted(_SUPPORTED_PLUGIN_API_VERSIONS))
    sentinel = object()
    api_version = getattr(plugin, "untaped_api_version", sentinel)
    if api_version is sentinel:
        raise ConfigError(
            f"plugin {plugin_id!r} is missing required untaped_api_version; "
            f"supported versions are {supported}"
        )
    if type(api_version) is not int:
        raise ConfigError(
            f"plugin {plugin_id!r} has invalid untaped_api_version: expected int "
            f"({supported}), got {api_version!r}"
        )
    if api_version not in _SUPPORTED_PLUGIN_API_VERSIONS:
        raise ConfigError(
            f"plugin {plugin_id!r} declares unsupported untaped_api_version "
            f"{api_version}; supported versions are {supported}"
        )
    return api_version


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
