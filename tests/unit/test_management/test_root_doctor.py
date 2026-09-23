"""Tests for the root ``untaped doctor`` command.

The root doctor runs OFFLINE (config-file reads plus in-process model
validation; never network I/O) with per-row failure isolation: invalid
settings for one capability surface as failed rows while every other row
still runs (the Jira-isolation acceptance case). Any failure or quarantine
exits nonzero.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from test_management.support import (
    ExtProfile,
    GithubProfile,
    GithubState,
    JiraProfile,
    StrictProfile,
    check,
    compose,
    make_spec,
    write_config,
)
from untaped import bootstrap
from untaped.capabilities.registry import CompositionResult, QuarantineRecord
from untaped.management.doctor import build_root_doctor_app
from untaped.settings import get_settings
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config")

_PASS = "pass"


def _doctor_app(*specs: object, quarantine: tuple[QuarantineRecord, ...] = ()) -> object:
    result = compose(*specs)  # type: ignore[arg-type]
    if quarantine:
        result = CompositionResult(capabilities=result.capabilities, quarantine=quarantine)
    return build_root_doctor_app(shell=bootstrap.SHELL_SPEC, result=result)


def _quarantine() -> QuarantineRecord:
    return QuarantineRecord(
        distribution="example-dist",
        entry_point="example_mod:provider",
        reason="duplicate-name",
        detail="duplicate capability name: 'ghost'",
    )


# ── happy path ───────────────────────────────────────────────────────────────


def test_all_pass_exits_zero(_isolated_config: Path) -> None:
    write_config(
        _isolated_config, "profiles:\n  default:\n    github:\n      base_url: https://g\n"
    )
    get_settings.cache_clear()
    app = _doctor_app(
        make_spec("github", profile_model=GithubProfile),
        make_spec("jira", profile_model=JiraProfile),
    )
    result = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert _PASS in result.stdout


def test_contributed_passing_check_reports_detail(_isolated_config: Path) -> None:
    app = _doctor_app(make_spec("ext", doctor_checks=(check("ext.auth", detail="token valid"),)))
    result = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert "ext.auth" in result.stdout
    assert "token valid" in result.stdout


# ── execution failures isolate per row ───────────────────────────────────────


def test_failing_check_does_not_block_other_rows(_isolated_config: Path) -> None:
    app = _doctor_app(
        make_spec(
            "ext",
            doctor_checks=(
                check("ext.auth", ok=False, detail="token rejected"),
                check("ext.latency", detail="fast"),
            ),
        )
    )
    result = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert result.exit_code == 1
    assert "ext.auth" in result.stdout
    assert "token rejected" in result.stdout
    assert "ext.latency" in result.stdout
    assert "1 of" in result.stderr


def test_raising_check_body_is_a_failed_row(_isolated_config: Path) -> None:
    from untaped.capabilities.registry import DoctorCheck

    def _boom(ctx: object) -> object:
        raise RuntimeError("kaput")

    broken = DoctorCheck(id="ext.broken", title="t", run=_boom)  # type: ignore[arg-type]
    app = _doctor_app(make_spec("ext", doctor_checks=(broken,)))
    result = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert result.exit_code == 1
    assert "ext.broken" in result.stdout
    assert "kaput" in result.stdout


def test_id_mismatch_and_bad_return_are_failed_rows(_isolated_config: Path) -> None:
    from untaped.capabilities.registry import DoctorCheck, DoctorResult

    def _wrong_id(ctx: object) -> DoctorResult:
        return DoctorResult(id="other.id", ok=True, detail="x")

    def _not_a_result(ctx: object) -> object:
        return "fine"

    app = _doctor_app(
        make_spec(
            "ext",
            doctor_checks=(
                DoctorCheck(id="ext.a", title="t", run=_wrong_id),  # type: ignore[arg-type]
                DoctorCheck(id="ext.b", title="t", run=_not_a_result),  # type: ignore[arg-type]
            ),
        )
    )
    result = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert result.exit_code == 1
    assert "ext.a" in result.stdout
    assert "ext.b" in result.stdout


# ── Jira isolation: invalid settings fail their rows only ────────────────────


def test_invalid_capability_settings_fail_their_rows_only(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n"
        "    github:\n      base_url: https://g\n"
        "    jira:\n      timeout: not-a-number\n",
    )
    get_settings.cache_clear()
    app = _doctor_app(
        make_spec(
            "github",
            profile_model=GithubProfile,
            doctor_checks=(check("github.auth", detail="github ok"),),
        ),
        make_spec("jira", profile_model=JiraProfile),
    )
    result = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert result.exit_code == 1
    assert "timeout" in result.stdout
    assert "github.auth" in result.stdout
    assert "github ok" in result.stdout


def test_missing_required_field_is_a_failed_row(_isolated_config: Path) -> None:
    app = _doctor_app(make_spec("strict", profile_model=StrictProfile))
    result = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert result.exit_code == 1
    assert "endpoint" in result.stdout


def test_unparseable_config_fails_config_row_without_blocking_others(
    _isolated_config: Path,
) -> None:
    write_config(_isolated_config, "{ invalid: [yaml\n")
    get_settings.cache_clear()
    app = _doctor_app(
        make_spec("ext", doctor_checks=(check("ext.auth", detail="still ran"),)),
        quarantine=(_quarantine(),),
    )
    result = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert result.exit_code == 1
    assert "could not parse" in result.stdout
    assert "duplicate-name" in result.stdout
    assert "ext.auth" in result.stdout


# ── quarantine rows fail the run ─────────────────────────────────────────────


def test_quarantine_row_fails_exit(_isolated_config: Path) -> None:
    app = _doctor_app(quarantine=(_quarantine(),))
    result = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert result.exit_code == 1
    assert "example-dist" in result.stdout
    assert "duplicate-name" in result.stdout
    raw = CliInvoker().invoke(app, ["--format", "raw", "--columns", "detail"])  # type: ignore[arg-type]
    assert raw.exit_code == 1
    assert "duplicate capability name: 'ghost'" in raw.stdout


def test_undefined_active_profile_fails_section_rows(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default: {}\nactive: ghost\n")
    get_settings.cache_clear()
    app = _doctor_app(make_spec("ext", profile_model=ExtProfile))
    result = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    assert result.exit_code == 1
    rows = {(row["check"], row["capability"]): row for row in json.loads(result.stdout)}
    assert rows[("profile", "untaped")]["status"] == "fail"
    assert "not defined" in rows[("profile", "untaped")]["detail"]
    assert rows[("settings", "ext")]["status"] == "fail"


# ── offline ──────────────────────────────────────────────────────────────────


def test_doctor_performs_no_network_io(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _blocked(*args: object, **kwargs: object) -> object:
        raise AssertionError("network I/O attempted")

    monkeypatch.setattr(socket, "socket", _blocked)
    app = _doctor_app(
        make_spec("github", profile_model=GithubProfile),
        make_spec("ext", doctor_checks=(check("ext.auth"),)),
    )
    result = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output


# ── core, env, state and profile rows ────────────────────────────────────────


def _rows(app: object) -> tuple[int, list[dict[str, str]]]:
    result = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    return result.exit_code, json.loads(result.stdout)


def _failed(rows: list[dict[str, str]]) -> dict[str, str]:
    return {row["title"]: row["detail"] for row in rows if row["status"] == "fail"}


def test_core_settings_rows_pass_on_empty_config(_isolated_config: Path) -> None:
    code, rows = _rows(_doctor_app())
    assert code == 0
    titles = [row["title"] for row in rows if row["capability"] == "untaped"]
    for title in ("validate log_level", "validate http", "validate ui", "resolve active profile"):
        assert title in titles


def test_unknown_ui_theme_fails_ui_row(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    ui:\n      theme: bogus\n")
    code, rows = _rows(_doctor_app())
    assert code == 1
    assert "unknown UI theme" in _failed(rows)["validate ui"]


def test_bad_env_override_fails_its_row_and_names_the_variable(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_HTTP__TIMEOUT", "abc")
    monkeypatch.setenv("UNTAPED_JIRA__TIMEOUT", "soon")
    code, rows = _rows(_doctor_app(make_spec("jira", profile_model=JiraProfile)))
    assert code == 1
    failed = _failed(rows)
    assert "UNTAPED_HTTP__TIMEOUT" in failed["validate http"]
    assert "UNTAPED_JIRA__TIMEOUT" in failed["validate settings"]
    assert "validate ui" not in failed


def test_non_mapping_config_root_fails_config_row(_isolated_config: Path) -> None:
    write_config(_isolated_config, "- a\n- b\n")
    code, rows = _rows(_doctor_app())
    assert code == 1
    assert "root must be a mapping" in _failed(rows)["load config file"]


def test_active_profile_missing_without_profiles_fails_profile_row(
    _isolated_config: Path,
) -> None:
    write_config(_isolated_config, "active: prod\n")
    code, rows = _rows(_doctor_app())
    assert code == 1
    assert "'prod'" in _failed(rows)["resolve active profile"]


def test_invalid_state_section_fails_state_row(_isolated_config: Path) -> None:
    write_config(_isolated_config, "github:\n  cursor: [not, a, string]\n")
    code, rows = _rows(
        _doctor_app(make_spec("github", profile_model=GithubProfile, state_model=GithubState))
    )
    assert code == 1
    assert "cursor" in _failed(rows)["validate state"]
