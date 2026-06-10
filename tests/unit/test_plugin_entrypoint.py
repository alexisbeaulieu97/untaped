"""Entry point and root-app integration checks for the workspace plugin."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from importlib.metadata import entry_points
from pathlib import Path

import pytest
from untaped import get_settings
from untaped.main import build_app
from untaped.plugins import PluginRegistry
from untaped.settings import reset_config_registry_for_tests
from untaped.testing import CliInvoker

from untaped_workspace.plugin import plugin as workspace_plugin


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)
    reset_config_registry_for_tests()
    get_settings.cache_clear()
    yield cfg
    os.environ.pop("UNTAPED_PROFILE", None)
    reset_config_registry_for_tests()
    get_settings.cache_clear()


def test_workspace_plugin_entry_point_is_declared() -> None:
    matches = [
        ep
        for ep in entry_points(group="untaped.plugins")
        if ep.name == "workspace" and ep.value == "untaped_workspace.plugin:plugin"
    ]

    assert matches


def test_workspace_plugin_declares_untaped_api_version() -> None:
    assert workspace_plugin.untaped_api_version == 2


def test_root_app_can_register_workspace_plugin() -> None:
    app = build_app(plugins=[workspace_plugin])

    result = CliInvoker().invoke(app, ["workspace", "--help"])

    assert result.exit_code == 0, result.output
    assert "Manage local git workspaces" in result.output


def test_workspace_plugin_registers_agent_skill() -> None:
    registry = PluginRegistry()

    workspace_plugin.register(registry)

    spec = registry.skills["untaped-workspace"]
    assert spec.description == "Use the untaped workspace plugin."
    assert spec.source.joinpath("SKILL.md").is_file()


def test_config_list_includes_registered_workspace_profile_settings() -> None:
    app = build_app(plugins=[workspace_plugin])

    result = CliInvoker().invoke(app, ["config", "list", "--format", "raw", "--columns", "key"])

    assert result.exit_code == 0, result.output
    keys = set(result.stdout.splitlines())
    assert "workspace.cache_dir" in keys
    assert "workspace.workspaces_dir" in keys
    assert "workspace.workspaces" not in keys


def test_top_level_workspace_state_is_loaded_without_clobbering_profile_settings(
    _isolate_config: Path,
) -> None:
    _isolate_config.write_text(
        """
        profiles:
          default:
            workspace:
              cache_dir: /from/profile
        workspace:
          cache_dir: /from/state
          workspaces:
            - name: prod
              path: /tmp/prod
        """
    )
    app = build_app(plugins=[workspace_plugin])

    result = CliInvoker().invoke(
        app, ["config", "list", "--format", "raw", "--columns", "key", "--columns", "value"]
    )
    settings = get_settings()

    assert result.exit_code == 0, result.output
    rows = set(result.stdout.splitlines())
    assert "workspace.cache_dir\t/from/profile" in rows
    assert "workspace.workspaces" not in {row.split("\t", maxsplit=1)[0] for row in rows}
    assert settings.workspace.cache_dir == Path("/from/profile")
    assert [w.name for w in settings.workspace.workspaces] == ["prod"]


def test_command_local_profile_flag_controls_workspace_init_settings(
    _isolate_config: Path,
    tmp_path: Path,
) -> None:
    default_root = tmp_path / "default-workspaces"
    stage_root = tmp_path / "stage-workspaces"
    _isolate_config.write_text(
        f"""
        profiles:
          default:
            workspace:
              workspaces_dir: {default_root}
          stage:
            workspace:
              workspaces_dir: {stage_root}
        """
    )
    app = build_app(plugins=[workspace_plugin])

    result = CliInvoker().invoke(app, ["workspace", "init", "prod", "--profile", "stage"])

    assert result.exit_code == 0, result.output
    assert (stage_root / "prod" / "untaped.yml").is_file()
    assert not (default_root / "prod").exists()


def test_command_local_profile_flag_is_accepted_by_workspace_list(
    _isolate_config: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "prod"
    _isolate_config.write_text(
        f"""
        profiles:
          default: {{}}
          stage: {{}}
        workspace:
          workspaces:
            - name: prod
              path: {target}
        """
    )
    app = build_app(plugins=[workspace_plugin])

    result = CliInvoker().invoke(
        app,
        ["workspace", "list", "--format", "raw", "--columns", "name", "--profile", "stage"],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["prod"]


def test_workspace_list_honors_global_ui_collection_view_for_table_output(
    _isolate_config: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "prod"
    _isolate_config.write_text(
        f"""
        ui:
          collection_view: list
        workspace:
          workspaces:
            - name: prod
              path: {target}
        """
    )
    app = build_app(plugins=[workspace_plugin])

    result = CliInvoker().invoke(
        app,
        [
            "workspace",
            "list",
            "--format",
            "table",
            "--columns",
            "name",
            "--columns",
            "path",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "name: prod" in result.stdout
    assert f"path: {target.resolve()}" in result.stdout
    assert "╭" not in result.stdout
    assert "┌" not in result.stdout


def test_workspace_list_raw_ignores_unknown_global_ui_theme(
    _isolate_config: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "prod"
    _isolate_config.write_text(
        f"""
        ui:
          theme: missing
        workspace:
          workspaces:
            - name: prod
              path: {target}
        """
    )
    app = build_app(plugins=[workspace_plugin])

    result = CliInvoker().invoke(
        app,
        ["workspace", "list", "--format", "raw", "--columns", "name"],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["prod"]
    assert "\x1b[" not in result.stdout


def test_command_local_profile_flag_is_accepted_by_workspace_show(
    _isolate_config: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "prod"
    target.mkdir()
    (target / "untaped.yml").write_text("name: prod\nrepos: []\n")
    _isolate_config.write_text(
        f"""
        profiles:
          default: {{}}
          stage: {{}}
        workspace:
          workspaces:
            - name: prod
              path: {target}
        """
    )
    app = build_app(plugins=[workspace_plugin])

    result = CliInvoker().invoke(
        app, ["workspace", "show", "--workspace", "prod", "--profile", "stage", "--format", "json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload[0]["workspace"] == "prod"
    assert payload[0]["path"] == str(target.resolve())


def test_command_local_profile_flag_is_accepted_by_workspace_status(
    _isolate_config: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "prod"
    target.mkdir()
    (target / "untaped.yml").write_text("name: prod\nrepos: []\n")
    _isolate_config.write_text(
        f"""
        profiles:
          default: {{}}
          stage: {{}}
        workspace:
          workspaces:
            - name: prod
              path: {target}
        """
    )
    app = build_app(plugins=[workspace_plugin])

    result = CliInvoker().invoke(
        app,
        ["workspace", "status", "--workspace", "prod", "--profile", "stage", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []


def test_command_local_profile_flag_controls_workspace_sync_cache_dir(
    _isolate_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "prod"
    target.mkdir()
    (target / "untaped.yml").write_text(
        "name: prod\nrepos:\n  - url: https://github.com/example/api.git\n"
    )
    stage_cache = tmp_path / "stage-cache"
    _isolate_config.write_text(
        f"""
        profiles:
          default:
            workspace:
              cache_dir: {tmp_path / "default-cache"}
          stage:
            workspace:
              cache_dir: {stage_cache}
        workspace:
          workspaces:
            - name: prod
              path: {target}
        """
    )
    captured: dict[str, object] = {}

    class _SyncStub:
        def __init__(
            self,
            manifests: object,
            git: object,
            *,
            fs: object,
            cache_dir: Path,
        ) -> None:
            captured["cache_dir"] = cache_dir

        def __call__(self, workspace: object, **kwargs: object) -> list[object]:
            return []

    monkeypatch.setattr("untaped_workspace.cli.ops_commands.SyncWorkspace", _SyncStub)
    app = build_app(plugins=[workspace_plugin])

    result = CliInvoker().invoke(
        app,
        ["workspace", "sync", "--workspace", "prod", "--profile", "stage", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    assert captured["cache_dir"] == stage_cache


@pytest.mark.parametrize(
    "args",
    [
        ["workspace", "list"],
        ["workspace", "show"],
        ["workspace", "branch", "set"],
        ["workspace", "branch", "unset"],
        ["workspace", "branch", "apply"],
        ["workspace", "init"],
        ["workspace", "adopt"],
        ["workspace", "forget"],
        ["workspace", "add"],
        ["workspace", "remove"],
        ["workspace", "sync"],
        ["workspace", "status"],
        ["workspace", "foreach"],
        ["workspace", "import"],
        ["workspace", "path"],
        ["workspace", "edit"],
    ],
)
def test_registry_or_settings_commands_expose_command_local_profile(
    args: list[str],
) -> None:
    app = build_app(plugins=[workspace_plugin])

    result = CliInvoker().invoke(app, [*args, "--help"])

    assert result.exit_code == 0, result.output
    assert "--profile" in result.output


def test_shell_init_does_not_expose_command_local_profile() -> None:
    app = build_app(plugins=[workspace_plugin])

    result = CliInvoker().invoke(app, ["workspace", "shell-init", "--help"])

    assert result.exit_code == 0, result.output
    assert "Override the active profile for this command only" not in result.output
