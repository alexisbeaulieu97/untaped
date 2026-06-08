"""Plugin CLI tests for `plugins add` behavior."""

from __future__ import annotations

from pathlib import Path
from threading import Event, Thread
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


def _record_core(_isolated_config: Path) -> None:
    _isolated_config.write_text("plugins:\n  tool:\n    spec: untaped\n    editable: false\n")


def _record_successful_uv_calls(calls: list[list[str]], requirements: list[str]) -> Any:
    def _run(cmd: list[str], **_: Any) -> Any:
        calls.append(cmd)
        if cmd[:3] == ["uv", "pip", "compile"]:
            requirements.append(Path(cmd[3]).read_text())
            Path(cmd[5]).write_text("# resolved\n")
        return type("Result", (), {"returncode": 0})()

    return _run


def _write_plugin_project(path: Path, name: str) -> None:
    path.mkdir(parents=True)
    (path / "pyproject.toml").write_text(f"[project]\nname = {name!r}\nversion = '0.1.0'\n")


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


def test_plugins_add_no_sync_records_package_spec(_isolated_config: Path) -> None:
    result = CliRunner().invoke(plugins_app, ["add", "untaped-awx", "--no-sync"])

    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [{"spec": "untaped-awx", "editable": False}]


def test_plugins_add_success_message_falls_back_when_global_ui_theme_unknown(
    _isolated_config: Path,
) -> None:
    _isolated_config.write_text("ui:\n  theme: missing\n")

    result = CliRunner().invoke(plugins_app, ["add", "untaped-awx", "--no-sync"])

    assert result.exit_code == 0, result.output
    assert "added plugin package: untaped-awx" in result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [{"spec": "untaped-awx", "editable": False}]


def test_plugins_add_no_sync_records_multiple_package_specs(_isolated_config: Path) -> None:
    result = CliRunner().invoke(
        plugins_app,
        ["add", "untaped-awx", "untaped-profile", "--no-sync"],
    )

    assert result.exit_code == 0, result.output
    assert "added plugin package: untaped-awx" in result.output
    assert "added plugin package: untaped-profile" in result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [
        {"spec": "untaped-awx", "editable": False},
        {"spec": "untaped-profile", "editable": False},
    ]


def test_plugins_add_no_sync_reads_package_specs_from_stdin(_isolated_config: Path) -> None:
    result = CliRunner().invoke(
        plugins_app,
        ["add", "--stdin", "--no-sync"],
        input="untaped-awx\nuntaped-profile\n",
    )

    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [
        {"spec": "untaped-awx", "editable": False},
        {"spec": "untaped-profile", "editable": False},
    ]


def test_plugins_add_rejects_positional_and_stdin(_isolated_config: Path) -> None:
    result = CliRunner().invoke(
        plugins_app,
        ["add", "untaped-awx", "--stdin", "--no-sync"],
        input="untaped-profile\n",
    )

    assert result.exit_code == 1
    assert "provide identifiers as positional args or via --stdin, not both" in result.output
    assert not _isolated_config.exists()


def test_plugins_add_with_no_args_shows_help_without_writing_config(
    _isolated_config: Path,
) -> None:
    result = CliRunner().invoke(plugins_app, ["add"])

    assert result.exit_code == 2
    assert "Usage: plugins add" in result.output
    assert "PACKAGE_SPECS" in result.output
    assert not _isolated_config.exists()


def test_plugins_add_replaces_existing_named_direct_reference(_isolated_config: Path) -> None:
    bad = "untaped-awx @ https://github.com/alexisbeaulieu97/untaped-awx.git"
    corrected = "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git"

    first = CliRunner().invoke(plugins_app, ["add", bad, "--no-sync"])
    second = CliRunner().invoke(plugins_app, ["add", corrected, "--no-sync"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [{"spec": corrected, "editable": False}]


def test_plugins_add_sync_exact_syncs_managed_venv(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    requirements: list[str] = []
    venv = _managed_venv(tmp_path, monkeypatch)
    _record_core(_isolated_config)

    monkeypatch.setattr(
        "untaped.plugin_sync.subprocess.run",
        _record_successful_uv_calls(calls, requirements),
    )

    result = CliRunner().invoke(plugins_app, ["add", "untaped-awx"])

    assert result.exit_code == 0, result.output
    _assert_managed_sync(calls, venv)
    assert requirements == ["untaped\nuntaped-awx\n"]
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [{"spec": "untaped-awx", "editable": False}]


def test_plugins_add_sync_requires_recorded_core_spec(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    _managed_venv(tmp_path, monkeypatch)

    def _run(cmd: list[str], **_: Any) -> Any:
        calls.append(cmd)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("untaped.plugin_sync.subprocess.run", _run)

    result = CliRunner().invoke(plugins_app, ["add", "untaped-awx"])

    assert result.exit_code == 1
    assert "managed untaped core install spec is not recorded" in result.output
    assert calls == []
    data = yaml.safe_load(_isolated_config.read_text())
    assert data == {}


def test_plugins_add_sync_batches_managed_venv_sync_once(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    requirements: list[str] = []
    venv = _managed_venv(tmp_path, monkeypatch)
    _record_core(_isolated_config)

    monkeypatch.setattr(
        "untaped.plugin_sync.subprocess.run",
        _record_successful_uv_calls(calls, requirements),
    )

    result = CliRunner().invoke(plugins_app, ["add", "untaped-awx", "untaped-profile"])

    assert result.exit_code == 0, result.output
    _assert_managed_sync(calls, venv)
    assert requirements == ["untaped\nuntaped-awx\nuntaped-profile\n"]
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [
        {"spec": "untaped-awx", "editable": False},
        {"spec": "untaped-profile", "editable": False},
    ]


def test_plugins_add_git_spec_records_only_requested_plugin_and_lets_uv_resolve_deps(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    requirements: list[str] = []
    venv = _managed_venv(tmp_path, monkeypatch)
    spec = "untaped-ansible @ git+https://github.com/alexisbeaulieu97/untaped-ansible.git@v0.1.0"
    _record_core(_isolated_config)

    monkeypatch.setattr(
        "untaped.plugin_sync.subprocess.run",
        _record_successful_uv_calls(calls, requirements),
    )

    result = CliRunner().invoke(plugins_app, ["add", spec])

    assert result.exit_code == 0, result.output
    _assert_managed_sync(calls, venv)
    assert requirements == [f"untaped\n{spec}\n"]
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [{"spec": spec, "editable": False}]


def test_plugins_add_sync_rolls_back_state_when_uv_fails(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _managed_venv(tmp_path, monkeypatch)
    _record_core(_isolated_config)
    calls = 0

    def _run(_: list[str], **__: Any) -> Any:
        nonlocal calls
        calls += 1
        return type("Result", (), {"returncode": 0 if calls < 3 else 2})()

    monkeypatch.setattr("untaped.plugin_sync.subprocess.run", _run)

    result = CliRunner().invoke(
        plugins_app,
        ["add", "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git"],
    )

    assert result.exit_code == 1
    assert "plugin sync failed with exit 2" in result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"] == {"tool": {"spec": "untaped", "editable": False}}


def test_plugins_add_sync_keeps_concurrent_plugin_state_change(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _managed_venv(tmp_path, monkeypatch)
    _record_core(_isolated_config)
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
        if worker is None:
            worker = Thread(target=_concurrent_write)
            worker.start()
            concurrent_done.wait(timeout=0.2)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("untaped.plugin_sync.subprocess.run", _run)

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
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    requirements: list[str] = []
    venv = _managed_venv(tmp_path, monkeypatch)
    plugin = tmp_path / "plugins" / "profile"
    _write_plugin_project(plugin, "untaped-profile")
    monkeypatch.chdir(tmp_path)
    _record_core(_isolated_config)

    monkeypatch.setattr(
        "untaped.plugin_sync.subprocess.run",
        _record_successful_uv_calls(calls, requirements),
    )

    result = CliRunner().invoke(plugins_app, ["add", "plugins/profile", "--editable"])

    assert result.exit_code == 0, result.output
    _assert_managed_sync(calls, venv)
    assert requirements == [f"untaped\n-e {plugin}\n"]
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [
        {"spec": str(plugin), "editable": True, "name": "untaped-profile"}
    ]


def test_plugins_add_local_path_maps_to_uv_with_stable_name(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    requirements: list[str] = []
    venv = _managed_venv(tmp_path, monkeypatch)
    plugin = tmp_path / "plugins" / "profile"
    _write_plugin_project(plugin, "untaped-profile")
    monkeypatch.chdir(tmp_path)
    _record_core(_isolated_config)

    monkeypatch.setattr(
        "untaped.plugin_sync.subprocess.run",
        _record_successful_uv_calls(calls, requirements),
    )

    result = CliRunner().invoke(plugins_app, ["add", "plugins/profile"])

    assert result.exit_code == 0, result.output
    _assert_managed_sync(calls, venv)
    assert requirements == [f"untaped\n{plugin}\n"]
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [
        {"spec": str(plugin), "editable": False, "name": "untaped-profile"}
    ]


def test_plugins_add_editable_invalid_pyproject_reports_config_error(
    _isolated_config: Path, tmp_path: Path
) -> None:
    plugin = tmp_path / "broken"
    plugin.mkdir()
    (plugin / "pyproject.toml").write_text("[project\n")

    result = CliRunner().invoke(
        plugins_app,
        ["add", str(plugin), "--editable", "--no-sync"],
    )

    assert result.exit_code == 1
    assert "could not parse editable plugin pyproject" in result.output
    assert not _isolated_config.exists()


def test_plugins_add_infers_name_from_bare_direct_url(_isolated_config: Path) -> None:
    result = CliRunner().invoke(
        plugins_app,
        ["add", "git+https://github.com/alexisbeaulieu97/untaped-awx.git", "--no-sync"],
    )

    assert result.exit_code == 0, result.output
    assert (
        "added plugin package: "
        "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git"
    ) in result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [
        {
            "spec": "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git",
            "editable": False,
        }
    ]


def test_plugins_add_infers_name_from_bare_direct_url_with_git_ref(
    _isolated_config: Path,
) -> None:
    result = CliRunner().invoke(
        plugins_app,
        [
            "add",
            "git+https://github.com/alexisbeaulieu97/untaped-profile.git@v1.2.3",
            "--no-sync",
        ],
    )

    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [
        {
            "spec": "untaped-profile @ "
            "git+https://github.com/alexisbeaulieu97/untaped-profile.git@v1.2.3",
            "editable": False,
        }
    ]


def test_plugins_add_bare_direct_url_replaces_existing_named_reference(
    _isolated_config: Path,
) -> None:
    named = "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git"
    corrected = "git+https://github.com/example/untaped-awx.git"

    first = CliRunner().invoke(plugins_app, ["add", named, "--no-sync"])
    second = CliRunner().invoke(plugins_app, ["add", corrected, "--no-sync"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [
        {
            "spec": "untaped-awx @ git+https://github.com/example/untaped-awx.git",
            "editable": False,
        }
    ]


def test_plugins_add_no_sync_does_not_canonicalize_unrelated_legacy_direct_url(
    _isolated_config: Path,
) -> None:
    legacy = "git+https://github.com/alexisbeaulieu97/untaped-profile.git"
    _isolated_config.write_text(
        f"plugins:\n  packages:\n    - spec: {legacy!r}\n      editable: false\n"
    )

    result = CliRunner().invoke(plugins_app, ["add", "untaped-awx", "--no-sync"])

    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [
        {"spec": legacy, "editable": False},
        {"spec": "untaped-awx", "editable": False},
    ]


def test_plugins_add_rejects_bare_direct_url_when_name_cannot_be_inferred(
    _isolated_config: Path,
) -> None:
    result = CliRunner().invoke(
        plugins_app,
        ["add", "git+https://github.com/alexisbeaulieu97/.git", "--no-sync"],
    )

    assert result.exit_code == 1
    assert "could not infer plugin name from direct URL" in result.output
    assert "use 'name @ url'" in result.output
    assert not _isolated_config.exists()


def test_plugins_add_batch_rejects_invalid_spec_before_changing_config(
    _isolated_config: Path,
) -> None:
    original = "plugins:\n  packages:\n    - spec: untaped-profile\n      editable: false\n"
    _isolated_config.write_text(original)

    result = CliRunner().invoke(
        plugins_app,
        [
            "add",
            "untaped-awx",
            "git+https://github.com/alexisbeaulieu97/.git",
            "--no-sync",
        ],
    )

    assert result.exit_code == 1
    assert "could not infer plugin name from direct URL" in result.output
    assert _isolated_config.read_text() == original
