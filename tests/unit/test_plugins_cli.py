from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from untaped.plugins import app as plugins_app
from untaped.settings import get_settings, reset_config_registry_for_tests


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    reset_config_registry_for_tests()
    get_settings.cache_clear()
    yield cfg
    reset_config_registry_for_tests()
    get_settings.cache_clear()


def test_plugins_add_no_sync_records_package_spec(_isolated_config: Path) -> None:
    result = CliRunner().invoke(plugins_app, ["add", "untaped-awx", "--no-sync"])

    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [{"spec": "untaped-awx", "editable": False}]


def test_plugins_add_sync_invokes_uv_tool_install(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def _run(cmd: list[str], **_: Any) -> Any:
        calls.append(cmd)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("untaped.plugins.subprocess.run", _run)

    result = CliRunner().invoke(plugins_app, ["add", "untaped-awx"])

    assert result.exit_code == 0, result.output
    assert calls == [["uv", "tool", "install", "untaped", "--with", "untaped-awx", "--force"]]


def test_plugins_add_editable_maps_to_uv_with_editable(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def _run(cmd: list[str], **_: Any) -> Any:
        calls.append(cmd)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("untaped.plugins.subprocess.run", _run)

    result = CliRunner().invoke(plugins_app, ["add", "../untaped-awx", "--editable"])

    assert result.exit_code == 0, result.output
    assert calls == [
        ["uv", "tool", "install", "untaped", "--with-editable", "../untaped-awx", "--force"]
    ]
