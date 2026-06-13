"""Tests for plugin API v4: root options and settings layout contributions."""

from __future__ import annotations

import pytest

from untaped import create_app
from untaped.errors import ConfigError
from untaped.plugins import (
    CliSpec,
    PluginManifest,
    PluginRegistry,
    RootOptionSpec,
    SettingsLayoutSpec,
    register_plugins,
)


def _v4_plugin(manifest: PluginManifest, plugin_id: str = "demo") -> object:
    class V4Plugin:
        id = plugin_id
        untaped_api_version = 4

        def manifest(self) -> PluginManifest:
            return manifest

    return V4Plugin()


def _v3_plugin(manifest: PluginManifest, plugin_id: str = "demo") -> object:
    class V3Plugin:
        id = plugin_id
        untaped_api_version = 3

        def manifest(self) -> PluginManifest:
            return manifest

    return V3Plugin()


def _profile_root_option() -> RootOptionSpec:
    return RootOptionSpec(
        name="--profile",
        help="Override the active profile.",
        handler_import_path="untaped_profile.root_option:apply",
    )


def test_v4_plugin_with_plain_manifest_registers() -> None:
    registry = PluginRegistry()
    manifest = PluginManifest(clis=(CliSpec(name="demo", app=create_app(name="demo")),))

    register_plugins(registry, [_v4_plugin(manifest)])

    assert registry.load_errors == []
    assert registry.plugin_ids == {"demo"}


def test_v4_manifest_registers_root_options() -> None:
    registry = PluginRegistry()
    option = _profile_root_option()

    register_plugins(registry, [_v4_plugin(PluginManifest(root_options=(option,)))])

    assert registry.load_errors == []
    assert registry.root_options == {"--profile": option}


def test_v4_manifest_registers_settings_layout() -> None:
    registry = PluginRegistry()
    layout = SettingsLayoutSpec(import_path="untaped_profile.layout:LAYOUT")

    register_plugins(registry, [_v4_plugin(PluginManifest(settings_layout=layout))])

    assert registry.load_errors == []
    assert registry.settings_layout == layout


def test_v3_manifest_with_root_options_is_rejected() -> None:
    registry = PluginRegistry()

    register_plugins(
        registry,
        [_v3_plugin(PluginManifest(root_options=(_profile_root_option(),)))],
    )

    assert registry.root_options == {}
    assert len(registry.load_errors) == 1
    assert "untaped_api_version 4" in registry.load_errors[0].error


def test_v3_manifest_with_settings_layout_is_rejected() -> None:
    registry = PluginRegistry()
    layout = SettingsLayoutSpec(import_path="untaped_profile.layout:LAYOUT")

    register_plugins(registry, [_v3_plugin(PluginManifest(settings_layout=layout))])

    assert registry.settings_layout is None
    assert len(registry.load_errors) == 1
    assert "untaped_api_version 4" in registry.load_errors[0].error


def test_duplicate_root_option_keeps_first_plugin() -> None:
    registry = PluginRegistry()
    first = _v4_plugin(PluginManifest(root_options=(_profile_root_option(),)), "first")
    second = _v4_plugin(PluginManifest(root_options=(_profile_root_option(),)), "second")

    register_plugins(registry, [first, second])

    assert registry.plugin_ids == {"first"}
    assert registry.root_options == {"--profile": _profile_root_option()}
    assert len(registry.load_errors) == 1
    assert "duplicate root option" in registry.load_errors[0].error


def test_second_settings_layout_keeps_first_plugin() -> None:
    registry = PluginRegistry()
    first_layout = SettingsLayoutSpec(import_path="pkg_a.layout:LAYOUT")
    second_layout = SettingsLayoutSpec(import_path="pkg_b.layout:LAYOUT")

    register_plugins(
        registry,
        [
            _v4_plugin(PluginManifest(settings_layout=first_layout), "first"),
            _v4_plugin(PluginManifest(settings_layout=second_layout), "second"),
        ],
    )

    assert registry.plugin_ids == {"first"}
    assert registry.settings_layout == first_layout
    assert len(registry.load_errors) == 1
    assert "settings layout" in registry.load_errors[0].error


def test_failed_v4_plugin_contributes_nothing() -> None:
    registry = PluginRegistry(reserved_cli_names={"config"})
    manifest = PluginManifest(
        root_options=(_profile_root_option(),),
        clis=(CliSpec(name="config", app=create_app(name="config")),),
    )

    register_plugins(registry, [_v4_plugin(manifest)])

    assert registry.plugin_ids == set()
    assert registry.root_options == {}
    assert len(registry.load_errors) == 1


def test_root_option_name_must_be_long_flag() -> None:
    with pytest.raises(ConfigError, match="--"):
        RootOptionSpec(name="profile", help="", handler_import_path="mod:attr")


def test_root_option_handler_import_path_is_validated() -> None:
    with pytest.raises(ConfigError, match="module:attribute"):
        RootOptionSpec(name="--profile", help="", handler_import_path="not-a-path")


def test_settings_layout_import_path_is_validated() -> None:
    with pytest.raises(ConfigError, match="module:attribute"):
        SettingsLayoutSpec(import_path="missing-colon")


def test_unsupported_version_message_lists_v4() -> None:
    registry = PluginRegistry()

    class V5Plugin:
        id = "future"
        untaped_api_version = 5

    register_plugins(registry, [V5Plugin()])

    assert len(registry.load_errors) == 1
    assert "2, 3, 4" in registry.load_errors[0].error
