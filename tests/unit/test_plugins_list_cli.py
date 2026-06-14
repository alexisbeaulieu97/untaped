"""Plugin CLI tests for `plugins list` behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from untaped.plugins import PluginRegistry, set_current_registry
from untaped.plugins import app as plugins_app
from untaped.testing import CliInvoker
from untaped.ui import ThemeSpec

pytestmark = pytest.mark.usefixtures("_isolated_config")


def test_plugins_list_reports_invalid_plugin_state_without_traceback(
    _isolated_config: Path,
) -> None:
    _isolated_config.write_text("plugins:\n  packages:\n    - spec: 123\n")

    result = CliInvoker().invoke(plugins_app, ["list"])

    assert result.exit_code == 1
    assert "invalid plugins config" in result.output
    assert "Traceback" not in result.output


def test_plugins_list_empty_table_shows_guiding_hint(_isolated_config: Path) -> None:
    set_current_registry(PluginRegistry())

    result = CliInvoker().invoke(plugins_app, ["list"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "No plugins installed" in result.stderr
    assert "untaped plugins add" in result.stderr


def test_plugins_list_empty_json_emits_array_without_hint(_isolated_config: Path) -> None:
    set_current_registry(PluginRegistry())

    result = CliInvoker().invoke(plugins_app, ["list", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []
    assert "No plugins installed" not in result.stderr


def test_plugins_list_defaults_to_table_output(_isolated_config: Path) -> None:
    _isolated_config.write_text(
        "plugins:\n"
        "  packages:\n"
        "    - spec: untaped-profile @ git+https://github.com/alexisbeaulieu97/untaped-profile.git\n"
        "      editable: false\n"
    )

    result = CliInvoker().invoke(plugins_app, ["list"])

    assert result.exit_code == 0, result.output
    assert "name" in result.output
    assert "status" in result.output
    assert "untaped-profile" in result.output
    assert "recorded" in result.output


def test_plugins_list_honours_global_ui_collection_view(_isolated_config: Path) -> None:
    _isolated_config.write_text(
        "ui:\n"
        "  collection_view: list\n"
        "plugins:\n"
        "  packages:\n"
        "    - spec: untaped-profile\n"
        "      editable: false\n"
    )

    result = CliInvoker().invoke(plugins_app, ["list"])

    assert result.exit_code == 0, result.output
    assert "name: untaped-profile" in result.output
    assert "status: recorded" in result.output
    assert "╭" not in result.output


def test_plugins_list_uses_theme_registered_by_plugin(_isolated_config: Path) -> None:
    registry = PluginRegistry()
    registry.add_theme("lines", ThemeSpec(collection_view="list"))
    set_current_registry(registry)
    _isolated_config.write_text(
        "ui:\n"
        "  theme: lines\n"
        "plugins:\n"
        "  packages:\n"
        "    - spec: untaped-profile\n"
        "      editable: false\n"
    )

    result = CliInvoker().invoke(plugins_app, ["list"])

    assert result.exit_code == 0, result.output
    assert "name: untaped-profile" in result.output
    assert "status: recorded" in result.output
    assert "╭" not in result.output


def test_plugins_list_raw_ignores_unknown_global_ui_theme(_isolated_config: Path) -> None:
    _isolated_config.write_text(
        "ui:\n"
        "  theme: missing\n"
        "plugins:\n"
        "  packages:\n"
        "    - spec: untaped-profile\n"
        "      editable: false\n"
    )

    result = CliInvoker().invoke(plugins_app, ["list", "--format", "raw"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["untaped-profile"]


def test_plugins_list_json_includes_combined_loaded_and_desired_rows(
    _isolated_config: Path,
) -> None:
    registry = PluginRegistry()
    registry.add_plugin_id("awx")
    registry.add_plugin_id("profile")
    set_current_registry(registry)
    _isolated_config.write_text(
        "plugins:\n"
        "  packages:\n"
        "    - spec: untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git\n"
        "      editable: false\n"
        "    - spec: untaped-profile @ git+https://github.com/alexisbeaulieu97/untaped-profile.git\n"
        "      editable: false\n"
    )

    result = CliInvoker().invoke(plugins_app, ["list", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [
        {
            "name": "untaped-awx",
            "status": "installed",
            "plugin_id": "awx",
            "editable": False,
            "spec": "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git",
        },
        {
            "name": "untaped-profile",
            "status": "installed",
            "plugin_id": "profile",
            "editable": False,
            "spec": "untaped-profile @ git+https://github.com/alexisbeaulieu97/untaped-profile.git",
        },
    ]


def test_plugins_list_json_coalesces_legacy_bare_direct_url_state(
    _isolated_config: Path,
) -> None:
    registry = PluginRegistry()
    registry.add_plugin_id("profile")
    set_current_registry(registry)
    _isolated_config.write_text(
        "plugins:\n"
        "  packages:\n"
        "    - spec: git+https://github.com/alexisbeaulieu97/untaped-profile.git\n"
        "      editable: false\n"
    )

    result = CliInvoker().invoke(plugins_app, ["list", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [
        {
            "name": "untaped-profile",
            "status": "installed",
            "plugin_id": "profile",
            "editable": False,
            "spec": "git+https://github.com/alexisbeaulieu97/untaped-profile.git",
        }
    ]


def test_plugins_list_json_includes_unmatched_loaded_and_recorded_rows(
    _isolated_config: Path,
) -> None:
    registry = PluginRegistry()
    registry.add_plugin_id("demo")
    set_current_registry(registry)
    _isolated_config.write_text(
        "plugins:\n"
        "  packages:\n"
        "    - spec: untaped-profile @ git+https://github.com/alexisbeaulieu97/untaped-profile.git\n"
        "      editable: false\n"
    )

    result = CliInvoker().invoke(plugins_app, ["list", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [
        {
            "name": "demo",
            "status": "loaded",
            "plugin_id": "demo",
            "editable": None,
            "spec": "",
        },
        {
            "name": "untaped-profile",
            "status": "recorded",
            "plugin_id": "",
            "editable": False,
            "spec": "untaped-profile @ git+https://github.com/alexisbeaulieu97/untaped-profile.git",
        },
    ]


def test_plugins_list_raw_omits_loaded_only_plugins(_isolated_config: Path) -> None:
    registry = PluginRegistry()
    registry.add_plugin_id("demo")
    registry.add_plugin_id("profile")
    set_current_registry(registry)
    _isolated_config.write_text(
        "plugins:\n"
        "  packages:\n"
        "    - spec: untaped-profile @ git+https://github.com/alexisbeaulieu97/untaped-profile.git\n"
        "      editable: false\n"
    )

    result = CliInvoker().invoke(plugins_app, ["list", "--format", "raw"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["untaped-profile"]


def test_plugins_list_raw_defaults_to_plugin_names(_isolated_config: Path) -> None:
    registry = PluginRegistry()
    registry.add_plugin_id("profile")
    set_current_registry(registry)
    _isolated_config.write_text(
        "plugins:\n"
        "  packages:\n"
        "    - spec: untaped-profile @ git+https://github.com/alexisbeaulieu97/untaped-profile.git\n"
        "      editable: false\n"
    )

    result = CliInvoker().invoke(plugins_app, ["list", "--format", "raw"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["untaped-profile"]


def test_plugins_list_columns_select_output_fields(_isolated_config: Path) -> None:
    _isolated_config.write_text(
        "plugins:\n"
        "  packages:\n"
        "    - spec: untaped-profile @ git+https://github.com/alexisbeaulieu97/untaped-profile.git\n"
        "      editable: false\n"
    )

    result = CliInvoker().invoke(
        plugins_app,
        ["list", "--format", "raw", "--columns", "name", "--columns", "spec"],
    )

    assert result.exit_code == 0, result.output
    assert result.output == (
        "untaped-profile\t"
        "untaped-profile @ git+https://github.com/alexisbeaulieu97/untaped-profile.git\n"
    )


def test_plugins_list_rejects_duplicate_recorded_plugin_names(
    _isolated_config: Path,
) -> None:
    _isolated_config.write_text(
        "plugins:\n"
        "  packages:\n"
        "    - spec: untaped-profile\n"
        "      editable: false\n"
        "    - spec: untaped-profile @ git+https://github.com/alexisbeaulieu97/untaped-profile.git\n"
        "      editable: false\n"
    )

    result = CliInvoker().invoke(plugins_app, ["list"])

    assert result.exit_code == 1
    assert "duplicate plugin package spec: untaped-profile" in result.output
    assert "Traceback" not in result.output
