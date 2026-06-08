"""Installer helper tests for the managed untaped environment."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from untaped.installer import (
    bootstrap_core_install,
    default_managed_venv_path,
    default_shim_path,
    record_core_install,
    write_shim,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_default_install_paths_respect_xdg_data_home_and_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert default_managed_venv_path() == tmp_path / "data" / "untaped" / "venv"
    assert default_shim_path() == tmp_path / "home" / ".local" / "bin" / "untaped"


def test_record_core_install_preserves_recorded_plugins(_isolated_config: Path) -> None:
    _isolated_config.write_text(
        "plugins:\n  packages:\n    - spec: untaped-awx\n      editable: false\n"
    )

    record_core_install("/src/untaped", editable=True)

    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"] == {
        "tool": {"spec": "/src/untaped", "editable": True},
        "packages": [{"spec": "untaped-awx", "editable": False}],
    }


def test_write_shim_execs_managed_venv_untaped(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    shim = tmp_path / "bin" / "untaped"

    write_shim(venv, shim)

    expected = f'#!/usr/bin/env sh\nexec {venv / "bin" / "untaped"} "$@"\n'
    assert shim.read_text() == expected
    assert shim.stat().st_mode & os.X_OK


def test_bootstrap_core_install_syncs_under_managed_env_lock(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    venv = tmp_path / "venv"
    shim = tmp_path / "bin" / "untaped"
    requirements = tmp_path / "requirements.in"
    resolved = tmp_path / "requirements.txt"
    _isolated_config.write_text(
        "plugins:\n  packages:\n    - spec: untaped-awx\n      editable: false\n"
    )

    def _run(cmd: list[str], **_: Any) -> Any:
        calls.append(cmd)
        if cmd == ["uv", "venv", str(venv)]:
            python = venv / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.touch()
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("untaped.installer.subprocess.run", _run)

    bootstrap_core_install(
        "/src/untaped",
        editable=True,
        venv=venv,
        shim=shim,
        requirements=requirements,
        resolved=resolved,
    )

    python = str(venv / "bin" / "python")
    assert calls == [
        ["uv", "venv", str(venv)],
        [
            "uv",
            "pip",
            "compile",
            str(requirements),
            "--output-file",
            str(resolved),
            "--python",
            python,
            "--no-sources-package",
            "untaped",
            "--no-sources-package",
            "untaped-awx",
            "--quiet",
        ],
        ["uv", "pip", "sync", "--python", python, "--strict", str(resolved)],
    ]
    assert requirements.read_text() == "-e /src/untaped\nuntaped-awx\n"
    data = yaml.safe_load(_isolated_config.read_text())
    assert data["plugins"] == {
        "tool": {"spec": "/src/untaped", "editable": True},
        "packages": [{"spec": "untaped-awx", "editable": False}],
    }
    assert shim.exists()


def test_bootstrap_core_install_failure_does_not_record_core_only_state(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    venv = tmp_path / "venv"
    python = venv / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    shim = tmp_path / "bin" / "untaped"
    requirements = tmp_path / "requirements.in"
    resolved = tmp_path / "requirements.txt"
    original = "plugins:\n  packages:\n    - spec: untaped-awx\n      editable: false\n"
    _isolated_config.write_text(original)

    def _run(cmd: list[str], **_: Any) -> Any:
        calls.append(cmd)
        if cmd[:3] == ["uv", "pip", "compile"]:
            return type("Result", (), {"returncode": 2})()
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("untaped.installer.subprocess.run", _run)

    with pytest.raises(SystemExit, match="core dependency resolution failed"):
        bootstrap_core_install(
            "/src/untaped",
            editable=True,
            venv=venv,
            shim=shim,
            requirements=requirements,
            resolved=resolved,
        )

    assert requirements.read_text() == "-e /src/untaped\nuntaped-awx\n"
    assert calls == [
        [
            "uv",
            "pip",
            "compile",
            str(requirements),
            "--output-file",
            str(resolved),
            "--python",
            str(python),
            "--no-sources-package",
            "untaped",
            "--no-sources-package",
            "untaped-awx",
            "--quiet",
        ],
    ]
    assert _isolated_config.read_text() == original
    assert not shim.exists()


def test_install_script_delegates_venv_mutation_to_locked_installer_helper() -> None:
    script = (REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")

    assert 'if [ -d "$core_spec" ]; then' in script
    assert "uv run python -m untaped.installer" in script
    assert "--sync" in script
    assert '--requirements "$requirements"' in script
    assert '--resolved "$resolved"' in script
    assert 'uv venv "$venv"' not in script
    assert 'uv pip compile "$requirements"' not in script
    assert 'uv pip sync --python "$venv/bin/python"' not in script
    assert '"$venv/bin/untaped" plugins sync' not in script
