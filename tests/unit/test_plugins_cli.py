from __future__ import annotations

import json
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

    monkeypatch.setattr("untaped.plugins.subprocess.run", _run)

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

    monkeypatch.setattr("untaped.plugins.subprocess.run", _run)

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

    monkeypatch.setattr("untaped.plugins.subprocess.run", _run)

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

    result = CliRunner().invoke(
        plugins_app,
        ["remove", "untaped-awx", "untaped-profile", "--no-sync"],
    )

    assert result.exit_code == 0, result.output
    assert "removed plugin package: untaped-awx" in result.output
    assert "removed plugin package: untaped-profile" in result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [{"spec": "untaped-workspace", "editable": False}]


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

    result = CliRunner().invoke(
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

    result = CliRunner().invoke(
        plugins_app,
        ["remove", "--stdin", "--no-sync"],
        input="untaped-awx\nuntaped-profile\n",
    )

    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == []


def test_plugins_remove_rejects_positional_and_stdin(_isolated_config: Path) -> None:
    _isolated_config.write_text(
        "plugins:\n  packages:\n    - spec: untaped-awx\n      editable: false\n"
    )

    result = CliRunner().invoke(
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

    result = CliRunner().invoke(
        plugins_app,
        ["remove", "untaped-awx", "untaped-missing", "--no-sync"],
    )

    assert result.exit_code == 1
    assert "plugin package is not recorded: untaped-missing" in result.output
    assert _isolated_config.read_text() == original


def test_plugins_remove_with_no_args_shows_help_without_writing_config(
    _isolated_config: Path,
) -> None:
    result = CliRunner().invoke(plugins_app, ["remove"])

    assert result.exit_code == 2
    assert "Usage: plugins remove" in result.output
    assert "PACKAGE_SPECS" in result.output
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

    result = CliRunner().invoke(plugins_app, ["remove", "untaped-profile", "--no-sync"])

    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == []


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

    result = CliRunner().invoke(plugins_app, ["remove", "untaped-awx", "--no-sync"])

    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [{"spec": legacy, "editable": False}]


def test_plugins_remove_sync_accepts_tool_spec_override(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    _isolated_config.write_text(
        "plugins:\n  packages:\n    - spec: untaped-awx\n      editable: false\n"
    )

    def _run(cmd: list[str], **_: Any) -> Any:
        calls.append(cmd)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("untaped.plugins.subprocess.run", _run)

    result = CliRunner().invoke(
        plugins_app,
        [
            "remove",
            "untaped-awx",
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
            "--force",
        ]
    ]
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"] == {
        "tool": {"spec": "/home/alexis/tools/untaped", "editable": True},
        "packages": [],
    }


def test_plugins_remove_sync_batches_uv_tool_install_once(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
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

    def _run(cmd: list[str], **_: Any) -> Any:
        calls.append(cmd)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("untaped.plugins.subprocess.run", _run)

    result = CliRunner().invoke(plugins_app, ["remove", "untaped-awx", "untaped-profile"])

    assert result.exit_code == 0, result.output
    assert calls == [
        [
            "uv",
            "tool",
            "install",
            "untaped",
            "--no-sources",
            "--with",
            "untaped-workspace",
            "--force",
        ]
    ]
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"]["packages"] == [{"spec": "untaped-workspace", "editable": False}]


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

    monkeypatch.setattr("untaped.plugins.subprocess.run", _run)

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

    monkeypatch.setattr("untaped.plugins.subprocess.run", _run)

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

    monkeypatch.setattr("untaped.plugins.subprocess.run", _run)

    result = CliRunner().invoke(plugins_app, ["sync"])

    assert result.exit_code == 1
    assert "could not infer plugin name from direct URL" in result.output
    assert calls == []


def test_plugins_list_reports_invalid_plugin_state_without_traceback(
    _isolated_config: Path,
) -> None:
    _isolated_config.write_text("plugins:\n  packages:\n    - spec: 123\n")

    result = CliRunner().invoke(plugins_app, ["list"])

    assert result.exit_code == 1
    assert "invalid plugins config" in result.output
    assert "Traceback" not in result.output


def test_plugins_list_defaults_to_table_output(_isolated_config: Path) -> None:
    _isolated_config.write_text(
        "plugins:\n"
        "  packages:\n"
        "    - spec: untaped-profile @ git+https://github.com/alexisbeaulieu97/untaped-profile.git\n"
        "      editable: false\n"
    )

    result = CliRunner().invoke(plugins_app, ["list"])

    assert result.exit_code == 0, result.output
    assert "name" in result.output
    assert "status" in result.output
    assert "untaped-profile" in result.output
    assert "recorded" in result.output


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

    result = CliRunner().invoke(plugins_app, ["list", "--format", "json"])

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

    result = CliRunner().invoke(plugins_app, ["list", "--format", "json"])

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

    result = CliRunner().invoke(plugins_app, ["list", "--format", "json"])

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

    result = CliRunner().invoke(plugins_app, ["list", "--format", "raw"])

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

    result = CliRunner().invoke(plugins_app, ["list", "--format", "raw"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["untaped-profile"]


def test_plugins_list_columns_select_output_fields(_isolated_config: Path) -> None:
    _isolated_config.write_text(
        "plugins:\n"
        "  packages:\n"
        "    - spec: untaped-profile @ git+https://github.com/alexisbeaulieu97/untaped-profile.git\n"
        "      editable: false\n"
    )

    result = CliRunner().invoke(
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

    result = CliRunner().invoke(plugins_app, ["list"])

    assert result.exit_code == 1
    assert "duplicate plugin package spec: untaped-profile" in result.output
    assert "Traceback" not in result.output


def test_plugins_doctor_success_path_reports_ok_diagnostics(_isolated_config: Path) -> None:
    registry = PluginRegistry()
    registry.add_plugin_id("demo")
    registry.add_diagnostic("demo", lambda: DiagnosticResult(name="demo", status="ok"))
    set_current_registry(registry)

    result = CliRunner().invoke(plugins_app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["ok\tdemo"]
