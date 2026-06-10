"""Tests for declarative manifest-based plugin registration (api version 3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr

from untaped import create_app
from untaped.errors import ConfigError
from untaped.plugins import (
    CliSpec,
    DiagnosticResult,
    PluginManifest,
    PluginRegistry,
    SkillSpec,
    register_plugins,
)
from untaped.ui import ThemeSpec


class DemoSettings(BaseModel):
    token: SecretStr | None = None


class DemoState(BaseModel):
    entries: list[str] = []


def _skill_dir(tmp_path: Path, name: str = "untaped-demo") -> Path:
    source = tmp_path / name
    source.mkdir()
    source.joinpath("SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: Teach agents how to use demo untaped commands.\n"
        "---\n"
        "\n"
        "# Demo\n",
    )
    return source


def _manifest_plugin(manifest: PluginManifest, plugin_id: str = "demo") -> object:
    class ManifestPlugin:
        id = plugin_id
        untaped_api_version = 3

        def manifest(self) -> PluginManifest:
            return manifest

    return ManifestPlugin()


def test_manifest_plugin_registers_all_contributions(tmp_path: Path) -> None:
    source = _skill_dir(tmp_path)
    manifest = PluginManifest(
        clis=(CliSpec(name="demo", app=create_app(name="demo")),),
        profile_settings={"demo": DemoSettings},
        state_settings={"demostate": DemoState},
        themes={"demo": ThemeSpec(border="square")},
        skills=(
            SkillSpec(
                name="untaped-demo",
                source=source,
                description="Teach agents how to use demo untaped commands.",
            ),
        ),
        diagnostics={"demo": lambda: DiagnosticResult(name="demo", status="ok")},
    )
    registry = PluginRegistry()

    register_plugins(registry, [_manifest_plugin(manifest)])

    assert registry.load_errors == []
    assert registry.plugin_ids == {"demo"}
    assert sorted(registry.clis) == ["demo"]
    assert registry.profile_sections == {"demo": DemoSettings}
    assert registry.state_sections == {"demostate": DemoState}
    assert sorted(registry.themes) == ["demo"]
    assert sorted(registry.skills) == ["untaped-demo"]
    assert registry.run_diagnostics() == [DiagnosticResult(name="demo", status="ok")]


def test_manifest_failure_is_atomic() -> None:
    registry = PluginRegistry()
    registry.add_cli("taken", create_app(name="taken"))
    manifest = PluginManifest(
        clis=(CliSpec(name="taken", app=create_app(name="taken")),),
        themes={"demo": ThemeSpec(border="square")},
    )

    register_plugins(registry, [_manifest_plugin(manifest)])

    assert [error.name for error in registry.load_errors] == ["demo"]
    assert "duplicate CLI command" in registry.load_errors[0].error
    assert registry.themes == {}
    assert registry.plugin_ids == set()


def test_intra_manifest_duplicate_cli_names_fail_atomically() -> None:
    registry = PluginRegistry()
    manifest = PluginManifest(
        clis=(
            CliSpec(name="demo", app=create_app(name="demo")),
            CliSpec(name="demo", app=create_app(name="demo")),
        ),
    )

    register_plugins(registry, [_manifest_plugin(manifest)])

    assert [error.name for error in registry.load_errors] == ["demo"]
    assert registry.clis == {}


def test_v3_plugin_without_manifest_records_load_error() -> None:
    class NoManifestPlugin:
        id = "demo"
        untaped_api_version = 3

    registry = PluginRegistry()

    register_plugins(registry, [NoManifestPlugin()])

    assert registry.plugin_ids == set()
    assert [error.name for error in registry.load_errors] == ["demo"]
    assert "manifest()" in registry.load_errors[0].error


def test_v2_plugin_without_register_records_load_error() -> None:
    class NoRegisterPlugin:
        id = "demo"
        untaped_api_version = 2

    registry = PluginRegistry()

    register_plugins(registry, [NoRegisterPlugin()])

    assert registry.plugin_ids == set()
    assert [error.name for error in registry.load_errors] == ["demo"]
    assert "register()" in registry.load_errors[0].error


def test_v2_and_v3_plugins_coexist() -> None:
    class LegacyPlugin:
        id = "legacy"
        untaped_api_version = 2

        def register(self, registry: PluginRegistry) -> None:
            registry.add_cli("legacy", create_app(name="legacy"))

    manifest = PluginManifest(clis=(CliSpec(name="modern", app=create_app(name="modern")),))

    registry = PluginRegistry()
    register_plugins(registry, [LegacyPlugin(), _manifest_plugin(manifest, plugin_id="modern")])

    assert registry.load_errors == []
    assert sorted(registry.clis) == ["legacy", "modern"]
    assert registry.plugin_ids == {"legacy", "modern"}


def test_cli_spec_requires_exactly_one_source() -> None:
    with pytest.raises(ConfigError, match="exactly one of app or import_path"):
        CliSpec(name="demo")

    with pytest.raises(ConfigError, match="exactly one of app or import_path"):
        CliSpec(name="demo", app=create_app(name="demo"), import_path="pkg.cli:app")


def test_cli_spec_rejects_malformed_import_path() -> None:
    with pytest.raises(ConfigError, match="module:attribute"):
        CliSpec(name="demo", import_path="pkg.cli.app")


def test_lazy_cli_registers_without_importing_module() -> None:
    manifest = PluginManifest(
        clis=(CliSpec(name="demo", import_path="nonexistent_module_xyz.cli:app"),),
    )
    registry = PluginRegistry()

    register_plugins(registry, [_manifest_plugin(manifest)])

    assert registry.load_errors == []
    assert sorted(registry.lazy_clis) == ["demo"]
    assert "demo" not in registry.clis


def test_lazy_cli_name_collides_with_eager_cli() -> None:
    registry = PluginRegistry()
    registry.add_cli("demo", create_app(name="demo"))

    with pytest.raises(ConfigError, match="duplicate CLI command"):
        registry.add_lazy_cli(CliSpec(name="demo", import_path="pkg.cli:app"))


def test_eager_cli_name_collides_with_lazy_cli() -> None:
    registry = PluginRegistry()
    registry.add_lazy_cli(CliSpec(name="demo", import_path="pkg.cli:app"))

    with pytest.raises(ConfigError, match="duplicate CLI command"):
        registry.add_cli("demo", create_app(name="demo"))


def test_lazy_cli_rejects_reserved_names() -> None:
    registry = PluginRegistry(reserved_cli_names={"config"})

    with pytest.raises(ConfigError, match="reserved CLI command"):
        registry.add_lazy_cli(CliSpec(name="config", import_path="pkg.cli:app"))
