"""Legacy-install ownership detection (spec §8, Wave 1.6).

A stale standalone ``untaped-<name>`` copy on ``PATH`` (outside the
current environment's managed prefix) must be detected, must print the
exact remediation (``uv tool uninstall <command>``), and must stay
advisory-only: it never fails the run and never masks real failures.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from test_management.support import check, compose, make_spec
from untaped import bootstrap
from untaped.management.doctor import build_root_doctor_app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config")


def _doctor_app(*specs: object) -> object:
    result = compose(*specs)  # type: ignore[arg-type]
    return build_root_doctor_app(shell=bootstrap.SHELL_SPEC, result=result)


def _shim(bindir: Path, command: str) -> Path:
    bindir.mkdir(parents=True, exist_ok=True)
    shim = bindir / command
    shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    shim.chmod(0o755)
    return shim


def test_stale_install_on_path_is_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shim = _shim(tmp_path / "bin", "untaped-ghost")
    monkeypatch.setenv("PATH", str(shim.parent))
    result = CliInvoker().invoke(_doctor_app(make_spec("ghost")), [])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert "legacy-install" in result.stdout
    assert "untaped-ghost" in result.stdout
    assert "untaped ghost" in result.stdout


def test_exact_remediation_is_printed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shim = _shim(tmp_path / "bin", "untaped-ghost")
    monkeypatch.setenv("PATH", str(shim.parent))
    app = _doctor_app(make_spec("ghost"))
    result = CliInvoker().invoke(app, ["--format", "raw", "--columns", "detail"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert str(shim) in result.stdout
    assert "uv tool uninstall untaped-ghost" in result.stdout


def test_shadow_is_advisory_alongside_real_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _shim(tmp_path / "bin", "untaped-ghost")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    app = _doctor_app(
        make_spec("ghost", doctor_checks=(check("ghost.auth", ok=False, detail="boom"),))
    )
    result = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert result.exit_code == 1, result.output
    assert "ghost.auth" in result.stdout
    assert "legacy-install" in result.stdout
    raw = CliInvoker().invoke(app, ["--format", "raw", "--columns", "detail"])  # type: ignore[arg-type]
    assert raw.exit_code == 1, raw.output
    assert "uv tool uninstall untaped-ghost" in raw.stdout


def test_managed_prefix_hit_is_not_a_shadow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_prefix = tmp_path / "env"
    _shim(fake_prefix / "bin", "untaped-ghost")
    monkeypatch.setattr(sys, "prefix", str(fake_prefix))
    monkeypatch.setenv("PATH", str(fake_prefix / "bin"))
    result = CliInvoker().invoke(_doctor_app(make_spec("ghost")), [])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert "legacy-install" not in result.stdout


def test_empty_path_dir_reports_no_shadow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    result = CliInvoker().invoke(_doctor_app(make_spec("ghost")), [])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert "legacy-install" not in result.stdout
