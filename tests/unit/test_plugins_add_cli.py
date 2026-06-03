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


def test_plugins_add_no_sync_records_package_spec(_isolated_config: Path) -> None:
    result = CliRunner().invoke(plugins_app, ["add", "untaped-awx", "--no-sync"])

    assert result.exit_code == 0, result.output
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


def test_plugins_add_sync_invokes_uv_tool_install(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def _run(cmd: list[str], **_: Any) -> Any:
        calls.append(cmd)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("untaped.plugin_sync.subprocess.run", _run)

    result = CliRunner().invoke(plugins_app, ["add", "untaped-awx"])

    assert result.exit_code == 0, result.output
    assert calls == [
        ["uv", "tool", "install", "untaped", "--no-sources", "--with", "untaped-awx", "--force"]
    ]
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [{"spec": "untaped-awx", "editable": False}]


def test_plugins_add_sync_batches_uv_tool_install_once(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def _run(cmd: list[str], **_: Any) -> Any:
        calls.append(cmd)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("untaped.plugin_sync.subprocess.run", _run)

    result = CliRunner().invoke(plugins_app, ["add", "untaped-awx", "untaped-profile"])

    assert result.exit_code == 0, result.output
    assert calls == [
        [
            "uv",
            "tool",
            "install",
            "untaped",
            "--no-sources",
            "--with",
            "untaped-awx",
            "--with",
            "untaped-profile",
            "--force",
        ]
    ]
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [
        {"spec": "untaped-awx", "editable": False},
        {"spec": "untaped-profile", "editable": False},
    ]


def test_plugins_add_sync_accepts_tool_spec_override(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    package = "untaped-profile @ git+https://github.com/alexisbeaulieu97/untaped-profile.git"

    def _run(cmd: list[str], **_: Any) -> Any:
        calls.append(cmd)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("untaped.plugin_sync.subprocess.run", _run)

    result = CliRunner().invoke(
        plugins_app,
        [
            "add",
            package,
            "--tool-spec",
            "/home/alexis/tools/untaped",
            "--editable-tool",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        [
            "uv",
            "tool",
            "install",
            "/home/alexis/tools/untaped",
            "--editable",
            "--no-sources",
            "--with",
            package,
            "--force",
        ]
    ]
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"] == {
        "tool": {"spec": "/home/alexis/tools/untaped", "editable": True},
        "packages": [{"spec": package, "editable": False}],
    }


def test_plugins_add_sync_rolls_back_state_when_uv_fails(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _run(_: list[str], **__: Any) -> Any:
        return type("Result", (), {"returncode": 2})()

    monkeypatch.setattr("untaped.plugin_sync.subprocess.run", _run)

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
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def _run(cmd: list[str], **_: Any) -> Any:
        calls.append(cmd)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("untaped.plugin_sync.subprocess.run", _run)

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


def test_plugins_add_rejects_editable_tool_without_tool_spec(_isolated_config: Path) -> None:
    result = CliRunner().invoke(
        plugins_app,
        ["add", "untaped-awx", "--editable-tool", "--no-sync"],
    )

    assert result.exit_code == 1
    assert "--editable-tool requires --tool-spec" in result.output
    assert not _isolated_config.exists()
