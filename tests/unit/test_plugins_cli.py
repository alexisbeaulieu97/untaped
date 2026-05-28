from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from threading import Event, Thread
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from untaped.config_file import mutate_config
from untaped.plugins import DiagnosticResult, PluginRegistry, set_current_registry
from untaped.plugins import app as plugins_app
from untaped.settings import get_settings, reset_config_registry_for_tests


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    reset_config_registry_for_tests()
    set_current_registry(PluginRegistry())
    get_settings.cache_clear()
    yield cfg
    reset_config_registry_for_tests()
    set_current_registry(PluginRegistry())
    get_settings.cache_clear()


def test_plugins_add_no_sync_records_package_spec(_isolated_config: Path) -> None:
    result = CliRunner().invoke(plugins_app, ["add", "untaped-awx", "--no-sync"])

    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [{"spec": "untaped-awx", "editable": False}]


def test_plugins_add_replaces_existing_named_direct_reference(_isolated_config: Path) -> None:
    bad = "untaped-awx @ https://github.com/alexisbeaulieu97/untaped-awx.git"
    corrected = "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git"

    first = CliRunner().invoke(plugins_app, ["add", bad, "--no-sync"])
    second = CliRunner().invoke(plugins_app, ["add", corrected, "--no-sync"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [{"spec": corrected, "editable": False}]


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
    assert calls == [
        ["uv", "tool", "install", "untaped", "--no-sources", "--with", "untaped-awx", "--force"]
    ]
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [{"spec": "untaped-awx", "editable": False}]


def test_plugins_add_sync_rolls_back_state_when_uv_fails(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _run(_: list[str], **__: Any) -> Any:
        return type("Result", (), {"returncode": 2})()

    monkeypatch.setattr("untaped.plugins.subprocess.run", _run)

    result = CliRunner().invoke(
        plugins_app,
        ["add", "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git"],
    )

    assert result.exit_code == 1
    assert "plugin sync failed with exit 2" in result.output
    assert not _isolated_config.exists()


def test_plugins_add_sync_keeps_concurrent_plugin_state_change(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    concurrent_done = Event()
    worker: Thread | None = None

    def _record_github(data: dict[str, Any]) -> None:
        plugins = data.setdefault("plugins", {})
        assert isinstance(plugins, dict)
        packages = plugins.setdefault("packages", [])
        assert isinstance(packages, list)
        packages.append({"spec": "untaped-github", "editable": False})

    def _concurrent_write() -> None:
        mutate_config(_record_github)
        concurrent_done.set()

    def _run(_: list[str], **__: Any) -> Any:
        nonlocal worker
        worker = Thread(target=_concurrent_write)
        worker.start()
        concurrent_done.wait(timeout=0.2)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("untaped.plugins.subprocess.run", _run)

    result = CliRunner().invoke(plugins_app, ["add", "untaped-awx"])

    assert result.exit_code == 0, result.output
    assert worker is not None
    worker.join(timeout=1)
    assert not worker.is_alive()
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [
        {"spec": "untaped-awx", "editable": False},
        {"spec": "untaped-github", "editable": False},
    ]


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
        [
            "uv",
            "tool",
            "install",
            "untaped",
            "--no-sources",
            "--with-editable",
            "../untaped-awx",
            "--force",
        ]
    ]


def test_plugins_add_rejects_bare_direct_url(_isolated_config: Path) -> None:
    result = CliRunner().invoke(
        plugins_app,
        ["add", "git+https://github.com/alexisbeaulieu97/untaped-awx.git", "--no-sync"],
    )

    assert result.exit_code == 1
    assert "direct URL plugin specs must use 'name @ url'" in result.output
    assert not _isolated_config.exists()


def test_plugins_remove_accepts_package_name_for_named_direct_reference(
    _isolated_config: Path,
) -> None:
    spec = "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git"
    _isolated_config.write_text(
        f"plugins:\n  packages:\n    - spec: {spec!r}\n      editable: false\n"
    )

    result = CliRunner().invoke(plugins_app, ["remove", "untaped-awx", "--no-sync"])

    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == []


def test_plugins_sync_tool_spec_rolls_back_when_uv_fails(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_config.write_text(
        "plugins:\n  packages:\n    - spec: untaped-awx\n      editable: false\n"
    )

    def _run(_: list[str], **__: Any) -> Any:
        return type("Result", (), {"returncode": 2})()

    monkeypatch.setattr("untaped.plugins.subprocess.run", _run)

    result = CliRunner().invoke(
        plugins_app,
        ["sync", "--tool-spec", "git+https://github.com/alexisbeaulieu97/untaped.git"],
    )

    assert result.exit_code == 1
    assert "plugin sync failed with exit 2" in result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data == {"plugins": {"packages": [{"spec": "untaped-awx", "editable": False}]}}


def test_plugins_sync_rejects_recorded_bare_direct_url_before_uv(
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

    monkeypatch.setattr("untaped.plugins.subprocess.run", _run)

    result = CliRunner().invoke(plugins_app, ["sync"])

    assert result.exit_code == 1
    assert "direct URL plugin specs must use 'name @ url'" in result.output
    assert calls == []


def test_plugins_list_reports_invalid_plugin_state_without_traceback(
    _isolated_config: Path,
) -> None:
    _isolated_config.write_text("plugins:\n  packages:\n    - spec: 123\n")

    result = CliRunner().invoke(plugins_app, ["list"])

    assert result.exit_code == 1
    assert "invalid plugins config" in result.output
    assert "Traceback" not in result.output


def test_plugins_doctor_success_path_reports_ok_diagnostics(_isolated_config: Path) -> None:
    registry = PluginRegistry()
    registry.add_plugin_id("demo")
    registry.add_diagnostic("demo", lambda: DiagnosticResult(name="demo", status="ok"))
    set_current_registry(registry)

    result = CliRunner().invoke(plugins_app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["ok\tdemo"]
