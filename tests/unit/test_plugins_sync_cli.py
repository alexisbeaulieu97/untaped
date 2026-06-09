"""Plugin CLI tests for `plugins sync` behavior."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from untaped.config_file import mutate_config
from untaped.plugins import app as plugins_app

pytestmark = pytest.mark.usefixtures("_isolated_config")


def _managed_venv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_home = tmp_path / "data"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return data_home / "untaped" / "venv"


def _tool_config() -> str:
    return "  tool:\n    spec: untaped\n    editable: false\n"


def _record_successful_uv_calls(calls: list[list[str]], requirements: list[str]) -> Any:
    def _run(cmd: list[str], **_: Any) -> Any:
        calls.append(cmd)
        if cmd[:3] == ["uv", "pip", "compile"]:
            requirements.append(Path(cmd[3]).read_text())
            Path(cmd[5]).write_text("# resolved\n")
        return type("Result", (), {"returncode": 0})()

    return _run


def _assert_managed_sync(
    calls: list[list[str]],
    venv: Path,
) -> None:
    python = str(venv / "bin" / "python")
    assert len(calls) == 3
    assert calls[0] == ["uv", "venv", str(venv)]
    assert calls[1][:3] == ["uv", "pip", "compile"]
    assert calls[1][4:6] == ["--output-file", calls[1][5]]
    assert calls[1][6:] == ["--python", python, "--no-sources", "--quiet"]
    assert calls[2] == ["uv", "pip", "sync", "--python", python, "--strict", calls[1][5]]


def test_plugins_sync_rolls_back_when_uv_fails(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _managed_venv(tmp_path, monkeypatch)
    _isolated_config.write_text(
        f"plugins:\n{_tool_config()}  packages:\n    - spec: untaped-awx\n      editable: false\n"
    )
    calls = 0

    def _run(_: list[str], **__: Any) -> Any:
        nonlocal calls
        calls += 1
        return type("Result", (), {"returncode": 0 if calls < 3 else 2})()

    monkeypatch.setattr("untaped.plugin_sync.subprocess.run", _run)

    result = CliRunner().invoke(plugins_app, ["sync"])

    assert result.exit_code == 1
    assert "plugin sync failed with exit 2" in result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data == {
        "plugins": {
            "tool": {"spec": "untaped", "editable": False},
            "packages": [{"spec": "untaped-awx", "editable": False}],
        }
    }


def test_plugins_sync_requires_recorded_core_spec_before_uv(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    _managed_venv(tmp_path, monkeypatch)
    _isolated_config.write_text(
        "plugins:\n  packages:\n    - spec: untaped-awx\n      editable: false\n"
    )

    def _run(cmd: list[str], **_: Any) -> Any:
        calls.append(cmd)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("untaped.plugin_sync.subprocess.run", _run)

    result = CliRunner().invoke(plugins_app, ["sync"])

    assert result.exit_code == 1
    assert "managed untaped core install spec is not recorded" in result.output
    assert calls == []


def test_plugins_sync_canonicalizes_recorded_bare_direct_url_after_uv_success(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    requirements: list[str] = []
    venv = _managed_venv(tmp_path, monkeypatch)
    _isolated_config.write_text(
        "plugins:\n"
        f"{_tool_config()}"
        "  packages:\n"
        "    - spec: git+https://github.com/alexisbeaulieu97/untaped-awx.git\n"
        "      editable: false\n"
    )

    monkeypatch.setattr(
        "untaped.plugin_sync.subprocess.run",
        _record_successful_uv_calls(calls, requirements),
    )

    result = CliRunner().invoke(plugins_app, ["sync"])

    assert result.exit_code == 0, result.output
    _assert_managed_sync(calls, venv)
    assert requirements == [
        "untaped\nuntaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git\n"
    ]
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [
        {
            "spec": "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git",
            "editable": False,
        }
    ]


def test_plugins_sync_success_message_falls_back_when_global_ui_theme_unknown(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    requirements: list[str] = []
    venv = _managed_venv(tmp_path, monkeypatch)
    _isolated_config.write_text(
        "ui:\n"
        "  theme: missing\n"
        "plugins:\n"
        f"{_tool_config()}"
        "  packages:\n"
        "    - spec: untaped-awx\n"
        "      editable: false\n"
    )

    monkeypatch.setattr(
        "untaped.plugin_sync.subprocess.run",
        _record_successful_uv_calls(calls, requirements),
    )

    result = CliRunner().invoke(plugins_app, ["sync"])

    assert result.exit_code == 0, result.output
    assert "plugin environment synced" in result.output
    _assert_managed_sync(calls, venv)
    assert requirements == ["untaped\nuntaped-awx\n"]


def test_plugins_sync_reuses_existing_managed_venv(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    requirements: list[str] = []
    venv = _managed_venv(tmp_path, monkeypatch)
    python = venv / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    _isolated_config.write_text(
        f"plugins:\n{_tool_config()}  packages:\n    - spec: untaped-awx\n      editable: false\n"
    )

    monkeypatch.setattr(
        "untaped.plugin_sync.subprocess.run",
        _record_successful_uv_calls(calls, requirements),
    )

    result = CliRunner().invoke(plugins_app, ["sync"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 2
    assert calls[0][:3] == ["uv", "pip", "compile"]
    assert calls[0][6:] == [
        "--python",
        str(python),
        "--no-sources",
        "--quiet",
    ]
    assert calls[1] == [
        "uv",
        "pip",
        "sync",
        "--python",
        str(python),
        "--strict",
        calls[0][5],
    ]
    assert requirements == ["untaped\nuntaped-awx\n"]


def test_plugins_sync_replays_editable_local_path_from_different_cwd(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    requirements: list[str] = []
    venv = _managed_venv(tmp_path, monkeypatch)
    plugin = tmp_path / "plugins" / "untaped-profile"
    plugin.mkdir(parents=True)
    other_cwd = tmp_path / "other"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)
    _isolated_config.write_text(
        "plugins:\n"
        f"{_tool_config()}"
        "  packages:\n"
        f"    - spec: {str(plugin)!r}\n"
        "      editable: true\n"
        "      name: untaped-profile\n"
    )

    monkeypatch.setattr(
        "untaped.plugin_sync.subprocess.run",
        _record_successful_uv_calls(calls, requirements),
    )

    result = CliRunner().invoke(plugins_app, ["sync"])

    assert result.exit_code == 0, result.output
    _assert_managed_sync(calls, venv)
    assert requirements == [f"untaped\n-e {plugin}\n"]


def test_plugins_sync_does_not_emit_legacy_raw_path_as_no_sources_package(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    requirements: list[str] = []
    venv = _managed_venv(tmp_path, monkeypatch)
    plugin = tmp_path / "plugins" / "untaped-profile"
    plugin.mkdir(parents=True)
    _isolated_config.write_text(
        "plugins:\n"
        f"{_tool_config()}"
        "  packages:\n"
        f"    - spec: {str(plugin)!r}\n"
        "      editable: false\n"
    )

    monkeypatch.setattr(
        "untaped.plugin_sync.subprocess.run",
        _record_successful_uv_calls(calls, requirements),
    )

    result = CliRunner().invoke(plugins_app, ["sync"])

    assert result.exit_code == 0, result.output
    _assert_managed_sync(calls, venv)
    assert requirements == [f"untaped\n{plugin}\n"]


def test_plugins_sync_reads_state_after_acquiring_env_lock(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    requirements: list[str] = []
    _managed_venv(tmp_path, monkeypatch)
    _isolated_config.write_text(
        f"plugins:\n{_tool_config()}  packages:\n    - spec: untaped-awx\n      editable: false\n"
    )

    @contextmanager
    def _lock() -> Iterator[None]:
        def _record_profile(data: dict[str, Any]) -> None:
            plugins = data.setdefault("plugins", {})
            assert isinstance(plugins, dict)
            packages = plugins.setdefault("packages", [])
            assert isinstance(packages, list)
            packages.append({"spec": "untaped-profile", "editable": False})

        mutate_config(_record_profile)
        yield

    monkeypatch.setattr("untaped.plugins.managed_env_lock", _lock)
    monkeypatch.setattr(
        "untaped.plugin_sync.subprocess.run",
        _record_successful_uv_calls(calls, requirements),
    )

    result = CliRunner().invoke(plugins_app, ["sync"])

    assert result.exit_code == 0, result.output
    assert requirements == ["untaped\nuntaped-awx\nuntaped-profile\n"]


def test_plugins_sync_does_not_canonicalize_recorded_bare_direct_url_when_uv_fails(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _managed_venv(tmp_path, monkeypatch)
    original = (
        "plugins:\n"
        f"{_tool_config()}"
        "  packages:\n"
        "    - spec: git+https://github.com/alexisbeaulieu97/untaped-awx.git\n"
        "      editable: false\n"
    )
    _isolated_config.write_text(original)

    calls = 0

    def _run(_: list[str], **__: Any) -> Any:
        nonlocal calls
        calls += 1
        return type("Result", (), {"returncode": 0 if calls < 3 else 2})()

    monkeypatch.setattr("untaped.plugin_sync.subprocess.run", _run)

    result = CliRunner().invoke(plugins_app, ["sync"])

    assert result.exit_code == 1
    assert "plugin sync failed with exit 2" in result.output
    assert _isolated_config.read_text() == original


def test_plugins_sync_rejects_recorded_uninferable_bare_direct_url_before_uv(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    _managed_venv(tmp_path, monkeypatch)
    _isolated_config.write_text(
        "plugins:\n"
        f"{_tool_config()}"
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
