"""Entry point and root-app integration checks for the workspace plugin."""

from __future__ import annotations

import os
from collections.abc import Iterator
from importlib.metadata import entry_points
from pathlib import Path

import pytest
from typer.testing import CliRunner
from untaped import get_settings
from untaped.main import build_app
from untaped.settings import reset_config_registry_for_tests

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


def test_root_app_can_register_workspace_plugin() -> None:
    app = build_app(plugins=[workspace_plugin])

    result = CliRunner().invoke(app, ["workspace", "--help"])

    assert result.exit_code == 0, result.output
    assert "Manage local git workspaces" in result.output


def test_config_list_includes_registered_workspace_profile_settings() -> None:
    app = build_app(plugins=[workspace_plugin])

    result = CliRunner().invoke(app, ["config", "list", "--format", "raw", "--columns", "key"])

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

    result = CliRunner().invoke(
        app, ["config", "list", "--format", "raw", "--columns", "key", "--columns", "value"]
    )
    settings = get_settings()

    assert result.exit_code == 0, result.output
    rows = set(result.stdout.splitlines())
    assert "workspace.cache_dir\t/from/profile" in rows
    assert "workspace.workspaces" not in {row.split("\t", maxsplit=1)[0] for row in rows}
    assert settings.workspace.cache_dir == Path("/from/profile")
    assert [w.name for w in settings.workspace.workspaces] == ["prod"]
