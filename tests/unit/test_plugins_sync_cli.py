"""Plugin CLI tests for `plugins sync` behavior."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from untaped.plugins import app as plugins_app

pytestmark = pytest.mark.usefixtures("_isolated_config")


def test_plugins_sync_tool_spec_rolls_back_when_uv_fails(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_config.write_text(
        "plugins:\n  packages:\n    - spec: untaped-awx\n      editable: false\n"
    )

    def _run(_: list[str], **__: Any) -> Any:
        return type("Result", (), {"returncode": 2})()

    monkeypatch.setattr("untaped.plugin_sync.subprocess.run", _run)

    result = CliRunner().invoke(
        plugins_app,
        ["sync", "--tool-spec", "git+https://github.com/alexisbeaulieu97/untaped.git"],
    )

    assert result.exit_code == 1
    assert "plugin sync failed with exit 2" in result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data == {"plugins": {"packages": [{"spec": "untaped-awx", "editable": False}]}}


def test_plugins_sync_canonicalizes_recorded_bare_direct_url_after_uv_success(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    _isolated_config.write_text(
        "plugins:\n"
        "  packages:\n"
        "    - spec: git+https://github.com/alexisbeaulieu97/untaped-awx.git\n"
        "      editable: false\n"
    )

    def _run(cmd: list[str], **_: Any) -> Any:
        calls.append(cmd)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("untaped.plugin_sync.subprocess.run", _run)

    result = CliRunner().invoke(plugins_app, ["sync"])

    assert result.exit_code == 0, result.output
    assert calls == [
        [
            "uv",
            "tool",
            "install",
            "untaped",
            "--no-sources",
            "--with",
            "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git",
            "--force",
        ]
    ]
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [
        {
            "spec": "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git",
            "editable": False,
        }
    ]


def test_plugins_sync_does_not_canonicalize_recorded_bare_direct_url_when_uv_fails(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = (
        "plugins:\n"
        "  packages:\n"
        "    - spec: git+https://github.com/alexisbeaulieu97/untaped-awx.git\n"
        "      editable: false\n"
    )
    _isolated_config.write_text(original)

    def _run(_: list[str], **__: Any) -> Any:
        return type("Result", (), {"returncode": 2})()

    monkeypatch.setattr("untaped.plugin_sync.subprocess.run", _run)

    result = CliRunner().invoke(plugins_app, ["sync"])

    assert result.exit_code == 1
    assert "plugin sync failed with exit 2" in result.output
    assert _isolated_config.read_text() == original


def test_plugins_sync_rejects_recorded_uninferable_bare_direct_url_before_uv(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    _isolated_config.write_text(
        "plugins:\n"
        "  packages:\n"
        "    - spec: git+https://github.com/alexisbeaulieu97/.git\n"
        "      editable: false\n"
    )

    def _run(cmd: list[str], **_: Any) -> Any:
        calls.append(cmd)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("untaped.plugin_sync.subprocess.run", _run)

    result = CliRunner().invoke(plugins_app, ["sync"])

    assert result.exit_code == 1
    assert "could not infer plugin name from direct URL" in result.output
    assert calls == []
