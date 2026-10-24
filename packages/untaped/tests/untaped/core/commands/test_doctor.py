"""Tests for the root ``untaped doctor`` command.

The root doctor runs OFFLINE (config-file reads plus in-process model
validation; never network I/O) with per-row failure isolation: invalid
settings for one plugin surface as failed rows while every other row
still runs (the Jira-isolation acceptance case). Any failure or quarantine
exits nonzero.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from cyclopts import App
from pydantic import BaseModel

from core_test_support.commands.stores import install_fake_stores
from core_test_support.commands.support import (
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
from untaped.core.commands.doctor import build_root_doctor_app, collect_doctor_rows
from untaped.runtime import bootstrap
from untaped.runtime.registry import (
    CompositionResult,
    PluginCandidate,
    PluginSpec,
    QuarantineRecord,
    named_plugin_dir,
)
from untaped.runtime.settings import FORMAT_VERSION, get_settings
from untaped.sdk.storage.cache import Cache
from untaped.sdk.storage.data_file import write_lines
from untaped.sdk.storage.state import StateRows
from untaped.sdk.testing import CliInvoker, make_project, plugin_candidate, write_rows

pytestmark = pytest.mark.usefixtures("_isolated_config")

_PASS = "pass"


def _doctor_app(*specs: object, quarantine: tuple[QuarantineRecord, ...] = ()) -> object:
    result = compose(*specs)  # type: ignore[arg-type]
    if quarantine:
        result = CompositionResult(
            plugins=result.plugins, quarantine=quarantine, mount=result.mount
        )
    return build_root_doctor_app(
        shell=bootstrap.SHELL_SPEC, builtin_for=lambda _name: None, result=result
    )


def _quarantine() -> QuarantineRecord:
    return QuarantineRecord(
        name="ghost",
        distribution="example-dist",
        entry_point="example_mod:provider",
        reason="duplicate-name",
        detail="duplicate plugin name: 'ghost'",
    )


# ── happy path ───────────────────────────────────────────────────────────────


def test_all_pass_exits_zero(_isolated_config: Path) -> None:
    write_config(
        _isolated_config, "profiles:\n  default:\n    github:\n      base_url: https://g\n"
    )
    get_settings.cache_clear()
    app = _doctor_app(
        make_spec("github", settings=GithubProfile),
        make_spec("jira", settings=JiraProfile),
    )
    result = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert _PASS in result.stdout


def test_a_pass_preset_that_cannot_decrypt_is_a_failed_row(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    monkeypatch.setenv("STUB_MODE", "no-secret-key")
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    github:\n      base_url: https://g\n"
        "      token_command: [pass, show, untaped/default/github]\n",
    )
    get_settings.cache_clear()
    result = CliInvoker().invoke(
        _doctor_app(make_spec("github", settings=GithubProfile)),  # type: ignore[arg-type]
        ["--format", "json"],
    )
    assert result.exit_code != 0
    [row] = [r for r in json.loads(result.stdout) if r["title"] == "pass token store"]
    assert row["status"] == "fail"
    assert "no secret key" in row["detail"] and "pass init" in row["detail"]
    assert "pinentry" not in row["detail"]


def test_a_working_pass_and_a_missing_pass_are_reported(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    github:\n      base_url: https://g\n"
        "      token_command: [pass, show, hand/written]\n",
    )
    get_settings.cache_clear()
    app = _doctor_app(make_spec("github", settings=GithubProfile))
    result = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    [row] = [r for r in json.loads(result.stdout) if r["title"] == "pass token store"]
    assert row["status"] == "pass", "a hand-written pass command counts too"
    (tmp_path / "fake-bin" / "pass").unlink()
    result = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    [row] = [r for r in json.loads(result.stdout) if r["title"] == "pass token store"]
    assert (row["status"], row["detail"]) == ("fail", "pass is not installed")


def test_an_unused_pass_adds_no_doctor_row(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    monkeypatch.setenv("STUB_MODE", "no-secret-key")
    result = CliInvoker().invoke(_doctor_app(), ["--format", "json"])  # type: ignore[arg-type]
    assert all(r["title"] != "pass token store" for r in json.loads(result.stdout))


def test_contributed_passing_check_reports_detail(_isolated_config: Path) -> None:
    app = _doctor_app(make_spec("ext", doctor_checks=(check("ext.auth", detail="token valid"),)))
    result = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    row = next(r for r in json.loads(result.stdout) if r["check"] == "ext.auth")
    assert row["detail"] == "token valid"


def test_the_columns_table_shows_a_pass_row_detail(_isolated_config: Path) -> None:
    app = _doctor_app(
        make_spec(
            "ext",
            doctor_checks=(
                check("ext.auth", detail="token valid"),
                check("ext.legacy", warn=True, detail="old key set"),
            ),
        )
    )
    columns = ["--columns", "check,status,detail,fix"]
    table = CliInvoker().invoke(app, columns)  # type: ignore[arg-type]
    assert table.exit_code == 0, table.output
    header = [cell.strip() for cell in table.stdout.splitlines()[1].strip("│").split("│")]
    assert header == ["check", "status", "detail", "fix"]
    assert "token valid" in table.stdout
    assert "old key set" in table.stdout
    listed = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    row = next(r for r in json.loads(listed.stdout) if r["check"] == "ext.auth")
    assert row["detail"] == "token valid"


def test_contributed_warning_check_is_a_warn_row_that_exits_zero(
    _isolated_config: Path,
) -> None:
    app = _doctor_app(
        make_spec("ext", doctor_checks=(check("ext.legacy", warn=True, detail="old key set"),))
    )
    result = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    row = next(r for r in json.loads(result.stdout) if r["check"] == "ext.legacy")
    assert row["status"] == "warn"
    assert row["detail"] == "old key set"


def test_failed_check_wins_over_warn(_isolated_config: Path) -> None:
    app = _doctor_app(
        make_spec("ext", doctor_checks=(check("ext.auth", ok=False, warn=True, detail="bad"),))
    )
    result = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    assert result.exit_code == 1
    row = next(r for r in json.loads(result.stdout) if r["check"] == "ext.auth")
    assert row["status"] == "fail"


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
    assert "doctor:" in result.stderr and "1 fail" in result.stderr


def test_raising_check_body_is_a_failed_row(_isolated_config: Path) -> None:
    from untaped.sdk.health.doctor import DoctorCheck

    def _boom(ctx: object) -> object:
        raise RuntimeError("kaput")

    broken = DoctorCheck(id="ext.broken", title="t", run=_boom)  # type: ignore[arg-type]
    app = _doctor_app(make_spec("ext", doctor_checks=(broken,)))
    result = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert result.exit_code == 1
    assert "ext.broken" in result.stdout
    assert "kaput" in result.stdout


def test_id_mismatch_and_bad_return_are_failed_rows(_isolated_config: Path) -> None:
    from untaped.sdk.health.doctor import DoctorCheck, DoctorResult

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


def test_a_detail_that_is_not_a_string_fails_its_row_under_json(_isolated_config: Path) -> None:
    from untaped.sdk.health.doctor import DoctorCheck, DoctorResult

    def _none_detail(ctx: object) -> DoctorResult:
        return DoctorResult(id="ext.a", ok=False, detail=None)  # type: ignore[arg-type]

    def _int_detail(ctx: object) -> DoctorResult:
        return DoctorResult(id="ext.b", ok=True, detail=3)  # type: ignore[arg-type]

    app = _doctor_app(
        make_spec(
            "ext",
            doctor_checks=(
                DoctorCheck(id="ext.a", title="t", run=_none_detail),  # type: ignore[arg-type]
                DoctorCheck(id="ext.b", title="t", run=_int_detail),  # type: ignore[arg-type]
            ),
        )
    )
    result = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]

    rows = {row["check"]: row for row in json.loads(result.stdout)}
    assert result.exit_code == 1
    assert (rows["ext.a"]["status"], rows["ext.a"]["detail"]) == ("fail", "")
    assert rows["ext.b"]["status"] == "fail"
    assert rows["ext.b"]["detail"] == "check returned detail of type int, expected str"


# ── Jira isolation: invalid settings fail their rows only ────────────────────


def test_invalid_plugin_settings_fail_their_rows_only(_isolated_config: Path) -> None:
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
            settings=GithubProfile,
            doctor_checks=(check("github.auth", detail="github ok"),),
        ),
        make_spec("jira", settings=JiraProfile),
    )
    result = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert result.exit_code == 1
    assert "timeout" in result.stdout
    assert "github.auth" in result.stdout
    assert any("github.auth" in line and "✓" in line for line in result.stdout.splitlines())


def test_missing_required_field_is_a_failed_row(_isolated_config: Path) -> None:
    app = _doctor_app(make_spec("strict", settings=StrictProfile))
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
    assert "duplicate plugin name: 'ghost'" in raw.stdout


def _raises_cli_import_failed() -> App:
    raise RuntimeError("cli import failed")


def _returns_a_string() -> object:
    return "not-an-app"


def _deferred(name: str, factory: Callable[[], object]) -> PluginSpec:
    return replace(make_spec(name), help=f"{name} plugin.", app_factory=factory)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("factory", "detail"),
    [
        (_raises_cli_import_failed, "cli import failed"),
        (_returns_a_string, "returned str, expected cyclopts App"),
    ],
    ids=["raises", "non-app"],
)
def test_doctor_reports_a_failing_lazy_factory_as_a_quarantine_row(
    factory: Callable[[], object], detail: str
) -> None:
    result = bootstrap.compose_root(
        candidates=[
            plugin_candidate(_deferred("bad", factory), distribution="bad-dist"),
            plugin_candidate(_deferred("good", lambda: App(name="good"))),
        ]
    )
    rows = collect_doctor_rows(bootstrap.SHELL_SPEC, result)
    quarantine = [row for row in rows if row["check"] == "quarantine"]
    assert [(row["plugin"], row["title"]) for row in quarantine] == [("bad", "bad-app-factory")]
    assert quarantine[0]["status"] == "fail"
    assert detail in str(quarantine[0]["detail"])
    assert str(quarantine[0]["detail"]).endswith(" [distribution bad-dist, entry point bad]")


def test_doctor_names_each_quarantined_plugin(
    broken_first_party_candidates: Callable[[], tuple[PluginCandidate, ...]],
) -> None:
    result = bootstrap.compose_root(candidates=broken_first_party_candidates())
    rows = collect_doctor_rows(bootstrap.SHELL_SPEC, result)
    quarantine = [row for row in rows if row["check"] == "quarantine"]
    assert [(row["plugin"], row["title"]) for row in quarantine] == [
        ("awx", "malformed-entry-point"),
        ("jira", "malformed-entry-point"),
    ]
    assert str(quarantine[0]["detail"]).endswith(" [distribution untaped]")


def test_doctor_limits_factory_rows_to_the_requested_plugins() -> None:
    result = bootstrap.compose_root(
        candidates=[plugin_candidate(_deferred("bad", _returns_a_string))]
    )
    rows = collect_doctor_rows(bootstrap.SHELL_SPEC, result, plugins=frozenset({"other"}))
    assert [row for row in rows if row["check"] == "quarantine"] == []


def test_doctor_cli_exits_1_on_a_failing_lazy_factory() -> None:
    root = bootstrap.build_root_app(
        candidates=[plugin_candidate(_deferred("bad", _returns_a_string))]
    )
    result = CliInvoker().invoke(root.meta, ["doctor", "--format", "json"])
    assert result.exit_code == 1
    assert any(
        row["check"] == "quarantine" and row["title"] == "bad-app-factory"
        for row in json.loads(result.stdout)
    )


def test_undefined_active_profile_fails_section_rows(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default: {}\nactive: ghost\n")
    get_settings.cache_clear()
    app = _doctor_app(make_spec("ext", settings=ExtProfile))
    result = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    assert result.exit_code == 1
    rows = {(row["check"], row["plugin"]): row for row in json.loads(result.stdout)}
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
        make_spec("github", settings=GithubProfile),
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
    titles = [row["title"] for row in rows if row["plugin"] == "untaped"]
    for title in ("validate http", "validate ui", "resolve active profile"):
        assert title in titles
    assert "validate log_level" not in titles


def test_removed_log_level_is_reported_as_an_unknown_key(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    log_level: DEBUG\n")
    code, rows = _rows(_doctor_app())
    assert code == 0
    (row,) = [row for row in rows if row["check"] == "unknown-keys"]
    assert row["status"] == "warn"
    assert "profiles.default.log_level" in row["detail"]


def test_top_level_keys_other_than_active_and_profiles_are_unknown(
    _isolated_config: Path,
) -> None:
    """A top-level ``log_level`` or pre-8.0 state section is flagged, not read."""
    write_config(
        _isolated_config,
        "active: default\nprofiles:\n  default: {}\nlog_level: DEBUG\nworkspace:\n  x: 1\n",
    )
    code, rows = _rows(_doctor_app())
    assert code == 0
    (row,) = [row for row in rows if row["check"] == "unknown-keys"]
    assert row["status"] == "warn"
    assert row["detail"] == "ignored: log_level, workspace"


def test_unknown_ui_theme_fails_ui_row(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    ui:\n      theme: bogus\n")
    code, rows = _rows(_doctor_app())
    assert code == 1
    assert "unknown UI theme" in _failed(rows)["validate ui"]


def test_bad_env_override_fails_its_row_and_names_the_variable(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_HTTP__TIMEOUT_SECONDS", "abc")
    monkeypatch.setenv("UNTAPED_JIRA__TIMEOUT", "soon")
    code, rows = _rows(_doctor_app(make_spec("jira", settings=JiraProfile)))
    assert code == 1
    failed = _failed(rows)
    assert "UNTAPED_HTTP__TIMEOUT_SECONDS" in failed["validate http"]
    assert "UNTAPED_JIRA__TIMEOUT" in failed["validate settings"]
    assert "validate ui" not in failed


def test_non_mapping_config_root_fails_config_row(_isolated_config: Path) -> None:
    write_config(_isolated_config, "- a\n- b\n")
    code, rows = _rows(_doctor_app())
    assert code == 1
    assert "root must be a mapping" in _failed(rows)["load config file"]


def test_a_newer_format_fails_the_config_rows(_isolated_config: Path) -> None:
    newer = FORMAT_VERSION + 1
    write_config(_isolated_config, f"format_version: {newer}\nprofiles: {{}}\n")
    (_isolated_config.parent / "state.yml").write_text(f"format_version: {newer}\n")
    code, rows = _rows(_doctor_app())
    assert code == 1
    failed = _failed(rows)
    assert f"written by a newer untaped (format {newer}" in failed["load config file"]
    assert f"written by a newer untaped (format {newer}" in failed["load state file"]


def test_an_explicit_format_version_is_not_an_unknown_key(_isolated_config: Path) -> None:
    stamp = f"format_version: {FORMAT_VERSION}\n"
    write_config(_isolated_config, f"{stamp}profiles:\n  default: {{}}\n")
    (_isolated_config.parent / "state.yml").write_text(stamp)
    code, rows = _rows(_doctor_app(make_spec("github", settings=GithubProfile, state=GithubState)))
    assert code == 0, _failed(rows)
    (unknown,) = [row for row in rows if row["check"] == "unknown-keys"]
    assert unknown["status"] == _PASS


def test_active_profile_missing_without_profiles_fails_profile_row(
    _isolated_config: Path,
) -> None:
    write_config(_isolated_config, "active: prod\n")
    code, rows = _rows(_doctor_app())
    assert code == 1
    assert "'prod'" in _failed(rows)["resolve active profile"]


def test_invalid_state_in_state_file_names_the_file(_isolated_config: Path) -> None:
    state_file = _isolated_config.parent / "state.yml"
    state_file.write_text("github:\n  cursor: [not, a, string]\n")
    code, rows = _rows(_doctor_app(make_spec("github", settings=GithubProfile, state=GithubState)))
    assert code == 1
    assert str(state_file) in _failed(rows)["validate state"]


class _Row(BaseModel):
    name: str
    created_at: str


_ROWS = StateRows(
    "acme", "items", model=_Row, key_field="name", remove_hint="untaped acme rm {key}"
)
_HERE = StateRows.per_project("acme", "here", model=_Row, key_field="name")


def test_typed_rows_are_counted_and_a_newer_installs_fields_pass(_isolated_config: Path) -> None:
    write_rows(_ROWS, [{"name": "a", "created_at": "x", "pinned": True}])

    code, rows = _rows(_doctor_app(make_spec("acme", stores=(_ROWS,))))

    assert code == 0, _failed(rows)
    (state,) = [row for row in rows if row["title"] == "validate state" and row["plugin"] == "acme"]
    assert state["detail"] == "state OK; acme.items: 1 row"


def test_a_damaged_row_fails_with_its_fix(_isolated_config: Path) -> None:
    write_rows(_ROWS, [{"name": "a"}])

    code, rows = _rows(_doctor_app(make_spec("acme", stores=(_ROWS,))))

    assert code == 1
    assert _failed(rows)["validate state"] == (
        f"acme.items row a in {_isolated_config.parent / 'state.yml'} is damaged: "
        "created_at: Field required; correct that row by hand, or remove it with "
        "`untaped acme rm a`"
    )


def test_the_current_projects_rows_are_checked(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path / "PROJ-1")
    write_rows(_HERE.of(project), [{"name": "a", "created_at": "x"}, {"name": "b"}])
    spec = make_spec("acme", stores=(_HERE,))

    code, rows = _rows(_doctor_app(spec))
    assert code == 0, _failed(rows)

    monkeypatch.chdir(project.root)
    code, rows = _rows(_doctor_app(spec))
    assert code == 1
    assert "acme.here row b" in _failed(rows)["validate state"]


def test_unreadable_state_file_fails_its_row(_isolated_config: Path) -> None:
    state_file = _isolated_config.parent / "state.yml"
    state_file.write_text("github: [unclosed\n")
    code, rows = _rows(_doctor_app(make_spec("github", settings=GithubProfile, state=GithubState)))
    assert code == 1
    failed = _failed(rows)
    assert str(state_file) in failed["load state file"]
    assert failed["validate state"] == "state file could not be read"


def test_state_left_in_config_is_ignored(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The pre-8.0 layout (state at the top level of config.yml) is not read."""
    write_config(_isolated_config, "github:\n  cursor: [not, a, string]\n")
    code, rows = _rows(_doctor_app(make_spec("github", settings=GithubProfile, state=GithubState)))
    assert code == 0
    assert not [row for row in rows if row["check"] == "legacy-state"]
    (state,) = [row for row in rows if row["title"] == "validate state"]
    assert state["detail"] == "no state"
    (unknown,) = [row for row in rows if row["check"] == "unknown-keys"]
    assert unknown["status"] == "warn"
    assert "github" in unknown["detail"]
    assert "warning: plugin state" not in capsys.readouterr().err


def test_missing_ca_bundle_fails_http_row(_isolated_config: Path, tmp_path: Path) -> None:
    missing = tmp_path / "nope.pem"
    write_config(
        _isolated_config, f"profiles:\n  default:\n    http:\n      ca_bundle_path: {missing}\n"
    )
    code, rows = _rows(_doctor_app())
    assert code == 1
    assert str(missing) in _failed(rows)["validate http"]


@pytest.mark.parametrize("verify_hostname", ["true", "false"])
def test_invalid_ca_bundle_fails_http_row(
    _isolated_config: Path, tmp_path: Path, verify_hostname: str
) -> None:
    bundle = tmp_path / "garbage.pem"
    bundle.write_text("not a certificate")
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    http:\n"
        f"      ca_bundle_path: {bundle}\n      verify_hostname: {verify_hostname}\n",
    )
    code, rows = _rows(_doctor_app())
    assert code == 1
    assert str(bundle) in _failed(rows)["validate http"]


def test_quiet_keeps_the_checklist_but_mutes_its_footer(_isolated_config: Path) -> None:
    write_config(
        _isolated_config, "profiles:\n  default:\n    github:\n      base_url: https://g\n"
    )
    root = bootstrap.build_root_app(
        candidates=(plugin_candidate(make_spec("github", settings=GithubProfile)),)
    )
    loud = CliInvoker().invoke(root.meta, ["doctor"])
    quiet = CliInvoker().invoke(root.meta, ["-q", "doctor"])
    assert loud.exit_code == quiet.exit_code == 0, quiet.output
    assert quiet.stdout == loud.stdout
    assert "doctor:" in loud.stderr
    assert quiet.stderr == ""


# ── caches ───────────────────────────────────────────────────────────────────


def _caches_row(rows: list[dict[str, str]]) -> dict[str, str]:
    [row] = [row for row in rows if row["check"] == "caches"]
    return row


def test_the_caches_row_has_nothing_to_note_on_a_clean_home() -> None:
    code, rows = _rows(_doctor_app())
    assert code == 0
    assert (_caches_row(rows)["status"], _caches_row(rows)["detail"]) == (_PASS, "nothing to note")


def test_keys_of_another_version_only_pass_with_a_note() -> None:
    typed = Cache("acme", "items", label="items", max_age=timedelta(hours=1), model=_Row)
    plain = Cache("acme", "names", label="names", max_age=timedelta(hours=1), version=lambda: "2")
    stamp = {"key": "k", "version": None, "refreshed_at": "2026-10-01T00:00:00Z"}
    shape = typed._typing().shape  # type: ignore[union-attr]
    folder = typed._root() / "s"
    # k has this version's file beside another install's; j has only another's.
    write_lines(folder / "k.v00000000.json", {**stamp, "type": shape}, b"{}")
    write_lines(folder / "k.v11111111.json", {**stamp, "type": "other"}, b"{}")
    write_lines(folder / "j.v11111111.json", {**stamp, "key": "j", "type": "other"}, b"{}")
    write_lines(plain._root() / "s" / "k.json", {**stamp, "version": "1"}, b"[]")
    write_lines(plain._root() / "s" / "j.json", {**stamp, "key": "j", "version": "2"}, b"[]")
    # i's file of this version holds only a failed attempt, beside another version's
    # value; h has only a failed attempt, so no version of it was ever filled.
    attempt = {**stamp, "key": "i", "type": shape, "refreshed_at": None}
    write_lines(folder / "i.v00000000.json", attempt, None)
    write_lines(folder / "i.v11111111.json", {**stamp, "key": "i", "type": "other"}, b"{}")
    write_lines(folder / "h.v00000000.json", {**attempt, "key": "h"}, None)
    write_lines(plain._root() / "s" / "h.json", {**attempt, "key": "h"}, None)
    spec = make_spec("acme", caches=(typed, plain))

    code, rows = _rows(_doctor_app(spec))

    assert code == 0, _failed(rows)
    row = _caches_row(rows)
    assert row["status"] == _PASS
    assert row["detail"] == (
        "acme.items: 2 keys filled by another version, refilled when due; "
        "acme.names: 1 key filled by another version, refilled when due"
    )


def test_a_cache_whose_version_code_fails_is_named_and_doctor_goes_on() -> None:
    def broken() -> str:
        raise RuntimeError("no source")

    cache = Cache("acme", "names", label="names", max_age=timedelta(hours=1), version=broken)

    code, rows = _rows(_doctor_app(make_spec("acme", caches=(cache,))))

    assert code == 0, _failed(rows)
    assert _caches_row(rows)["detail"] == "acme.names: could not check its files: no source"


def test_an_uninstalled_plugins_caches_pass_with_a_note_and_warn_nowhere() -> None:
    folder = named_plugin_dir("gone") / "cached" / "items"
    folder.mkdir(parents=True)
    (folder / "k.json").write_bytes(b"x" * 2048)

    code, rows = _rows(_doctor_app(make_spec("acme")))

    assert code == 0, _failed(rows)
    row = _caches_row(rows)
    assert row["status"] == _PASS
    assert row["detail"].endswith(
        "cached: 2.0 KiB of a plugin not installed here (another install may use it, "
        "so only `untaped setup migrate-dirs` removes it)"
    )
    assert [row for row in rows if row["status"] == "warn"] == []
