"""Plugin CLI tests for `plugins remove` behavior."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from untaped.plugins import app as plugins_app
from untaped.testing import CliInvoker

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


def test_plugins_remove_accepts_package_name_for_named_direct_reference(
    _isolated_config: Path,
) -> None:
    spec = "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git"
    _isolated_config.write_text(
        f"plugins:\n  packages:\n    - spec: {spec!r}\n      editable: false\n"
    )

    result = CliInvoker().invoke(plugins_app, ["remove", "untaped-awx", "--no-sync"])

    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert "plugins" not in data


def test_plugins_remove_no_sync_removes_multiple_package_specs(_isolated_config: Path) -> None:
    _isolated_config.write_text(
        "plugins:\n"
        "  packages:\n"
        "    - spec: untaped-awx\n"
        "      editable: false\n"
        "    - spec: untaped-profile\n"
        "      editable: false\n"
        "    - spec: untaped-workspace\n"
        "      editable: false\n"
    )

    result = CliInvoker().invoke(
        plugins_app,
        ["remove", "untaped-awx", "untaped-profile", "--no-sync"],
    )

    assert result.exit_code == 0, result.output
    assert "removed plugin package: untaped-awx" in result.output
    assert "removed plugin package: untaped-profile" in result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [{"spec": "untaped-workspace", "editable": False}]


def test_plugins_remove_success_message_falls_back_when_global_ui_theme_unknown(
    _isolated_config: Path,
) -> None:
    _isolated_config.write_text(
        "ui:\n"
        "  theme: missing\n"
        "plugins:\n"
        "  packages:\n"
        "    - spec: untaped-awx\n"
        "      editable: false\n"
    )

    result = CliInvoker().invoke(plugins_app, ["remove", "untaped-awx", "--no-sync"])

    assert result.exit_code == 0, result.output
    assert "removed plugin package: untaped-awx" in result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert "plugins" not in data


def test_plugins_remove_no_sync_ignores_duplicate_package_specs(
    _isolated_config: Path,
) -> None:
    _isolated_config.write_text(
        "plugins:\n"
        "  packages:\n"
        "    - spec: untaped-awx\n"
        "      editable: false\n"
        "    - spec: untaped-profile\n"
        "      editable: false\n"
    )

    result = CliInvoker().invoke(
        plugins_app,
        ["remove", "untaped-awx", "untaped-awx", "--no-sync"],
    )

    assert result.exit_code == 0, result.output
    assert result.output.count("removed plugin package: untaped-awx") == 1
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [{"spec": "untaped-profile", "editable": False}]


def test_plugins_remove_no_sync_reads_package_specs_from_stdin(_isolated_config: Path) -> None:
    _isolated_config.write_text(
        "plugins:\n"
        "  packages:\n"
        "    - spec: untaped-awx\n"
        "      editable: false\n"
        "    - spec: untaped-profile\n"
        "      editable: false\n"
    )

    result = CliInvoker().invoke(
        plugins_app,
        ["remove", "--stdin", "--no-sync"],
        input="untaped-awx\nuntaped-profile\n",
    )

    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data is None or "plugins" not in data


def test_plugins_remove_rejects_positional_and_stdin(_isolated_config: Path) -> None:
    _isolated_config.write_text(
        "plugins:\n  packages:\n    - spec: untaped-awx\n      editable: false\n"
    )

    result = CliInvoker().invoke(
        plugins_app,
        ["remove", "untaped-awx", "--stdin", "--no-sync"],
        input="untaped-awx\n",
    )

    assert result.exit_code == 1
    assert "provide identifiers as positional args or via --stdin, not both" in result.output


def test_plugins_remove_missing_package_fails_without_changing_config(
    _isolated_config: Path,
) -> None:
    original = "plugins:\n  packages:\n    - spec: untaped-awx\n      editable: false\n"
    _isolated_config.write_text(original)

    result = CliInvoker().invoke(
        plugins_app,
        ["remove", "untaped-awx", "untaped-missing", "--no-sync"],
    )

    assert result.exit_code == 1
    assert "plugin package is not recorded: untaped-missing" in result.output
    assert _isolated_config.read_text() == original


def test_plugins_remove_with_no_args_shows_help_without_writing_config(
    _isolated_config: Path,
) -> None:
    result = CliInvoker().invoke(plugins_app, ["remove"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Usage: plugins remove" in result.stderr
    assert "Plugin package spec(s) to remove" in result.stderr
    assert not _isolated_config.exists()


def test_plugins_remove_accepts_package_name_for_legacy_bare_direct_url(
    _isolated_config: Path,
) -> None:
    _isolated_config.write_text(
        "plugins:\n"
        "  packages:\n"
        "    - spec: git+https://github.com/alexisbeaulieu97/untaped-profile.git\n"
        "      editable: false\n"
    )

    result = CliInvoker().invoke(plugins_app, ["remove", "untaped-profile", "--no-sync"])

    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert "plugins" not in data


def test_plugins_remove_accepts_package_name_for_editable_local_path(
    _isolated_config: Path, tmp_path: Path
) -> None:
    plugin = tmp_path / "untaped-profile"
    _isolated_config.write_text(
        "plugins:\n"
        "  packages:\n"
        f"    - spec: {str(plugin)!r}\n"
        "      editable: true\n"
        "      name: untaped-profile\n"
    )

    result = CliInvoker().invoke(plugins_app, ["remove", "untaped-profile", "--no-sync"])

    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert "plugins" not in data


def test_plugins_remove_sync_failure_restores_editable_path_removed_by_spec(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plugin = tmp_path / "untaped-profile"
    plugin.mkdir()
    calls: list[list[str]] = []
    venv = _managed_venv(tmp_path, monkeypatch)
    _isolated_config.write_text(
        "plugins:\n"
        f"{_tool_config()}"
        "  packages:\n"
        f"    - spec: {str(plugin)!r}\n"
        "      editable: true\n"
        "      name: untaped-profile\n"
    )

    def _run(cmd: list[str], **_: Any) -> Any:
        calls.append(cmd)
        if cmd[:3] == ["uv", "pip", "compile"]:
            Path(cmd[5]).write_text("# resolved\n")
            return type("Result", (), {"returncode": 0})()
        if cmd[:3] == ["uv", "pip", "sync"]:
            return type("Result", (), {"returncode": 2})()
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("untaped.plugin_sync.subprocess.run", _run)

    result = CliInvoker().invoke(plugins_app, ["remove", str(plugin)])

    assert result.exit_code == 1
    assert "plugin sync failed with exit 2" in result.output
    _assert_managed_sync(calls, venv)
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["tool"] == {"spec": "untaped", "editable": False}
    assert data["plugins"]["packages"] == [
        {"spec": str(plugin), "editable": True, "name": "untaped-profile"}
    ]


def test_plugins_remove_no_sync_does_not_canonicalize_unrelated_legacy_direct_url(
    _isolated_config: Path,
) -> None:
    legacy = "git+https://github.com/alexisbeaulieu97/untaped-profile.git"
    _isolated_config.write_text(
        "plugins:\n"
        "  packages:\n"
        "    - spec: untaped-awx\n"
        "      editable: false\n"
        f"    - spec: {legacy!r}\n"
        "      editable: false\n"
    )

    result = CliInvoker().invoke(plugins_app, ["remove", "untaped-awx", "--no-sync"])

    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [{"spec": legacy, "editable": False}]


def test_plugins_remove_sync_exact_syncs_remaining_recorded_plugins(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    requirements: list[str] = []
    venv = _managed_venv(tmp_path, monkeypatch)
    _isolated_config.write_text(
        "plugins:\n"
        f"{_tool_config()}"
        "  packages:\n"
        "    - spec: untaped-awx\n"
        "      editable: false\n"
        "    - spec: untaped-profile\n"
        "      editable: false\n"
        "    - spec: untaped-workspace\n"
        "      editable: false\n"
    )

    monkeypatch.setattr(
        "untaped.plugin_sync.subprocess.run",
        _record_successful_uv_calls(calls, requirements),
    )

    result = CliInvoker().invoke(plugins_app, ["remove", "untaped-awx", "untaped-profile"])

    assert result.exit_code == 0, result.output
    _assert_managed_sync(calls, venv)
    assert requirements == ["untaped\nuntaped-workspace\n"]
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [{"spec": "untaped-workspace", "editable": False}]
