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
from datetime import timedelta
from pathlib import Path
from typing import Annotated

import pytest
from pydantic import BaseModel

from core_test_support.commands.stores import install_fake_stores
from core_test_support.commands.support import (
    ExtProfile,
    GithubProfile,
    JiraProfile,
    StrictProfile,
    check,
    compose,
    ghost_candidates,
    make_plugin,
    root_group,
    write_config,
)
from untaped.core.commands.doctor import collect_doctor_rows
from untaped.runtime import bootstrap
from untaped.runtime.registry import (
    PluginCandidate,
    named_plugin_dir,
)
from untaped.runtime.settings import FORMAT_VERSION, STATE_FORMAT_VERSION, get_settings
from untaped.sdk import Command, Key, Plugin
from untaped.sdk.network._credential_store import CredentialRow, index
from untaped.sdk.storage.cache import Cache
from untaped.sdk.storage.data_file import write_lines
from untaped.sdk.storage.state import StateRows
from untaped.sdk.testing import CliInvoker, make_project, plugin_candidate, write_rows

pytestmark = pytest.mark.usefixtures("_isolated_config", "home_run")

_PASS = "pass"


def _doctor_app(*plugins: type[Plugin], ghost: bool = False) -> object:
    """``untaped doctor`` composed with ``plugins``; ``ghost`` adds a plugin quarantined by name."""
    ghosts = ghost_candidates("example-dist", "other-dist") if ghost else ()
    return root_group("doctor", *plugins, also=ghosts)


# ── happy path ───────────────────────────────────────────────────────────────


def test_all_pass_exits_zero(_isolated_config: Path) -> None:
    write_config(_isolated_config, "format_version: 2\ngithub:\n  base_url: https://g\n")
    get_settings.cache_clear()
    app = _doctor_app(
        make_plugin("github", settings=GithubProfile),
        make_plugin("jira", settings=JiraProfile),
    )
    result = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert _PASS in result.stdout


def _file_row(**values: object) -> None:
    """A stored ``github`` credential row, as ``auth login`` files one."""
    index().put(CredentialRow(id="a" * 64, plugin="github", name="token", **values))  # type: ignore[arg-type]


def test_a_pass_credential_that_cannot_decrypt_is_a_failed_row(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    monkeypatch.setenv("STUB_MODE", "no-secret-key")
    _file_row(backend="pass", entry="github/token/aaaa")
    result = CliInvoker().invoke(
        _doctor_app(make_plugin("github", settings=GithubProfile)),  # type: ignore[arg-type]
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
    _file_row(backend="command", argv=["pass", "show", "hand/written"])
    app = _doctor_app(make_plugin("github", settings=GithubProfile))
    result = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    [row] = [r for r in json.loads(result.stdout) if r["title"] == "pass token store"]
    assert row["status"] == "pass", "a hand-written pass command counts too"
    (tmp_path / "fake-bin" / "pass").unlink()
    result = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    [row] = [r for r in json.loads(result.stdout) if r["title"] == "pass token store"]
    assert (row["status"], row["detail"]) == ("fail", "pass is not installed")


def test_an_unused_pass_fails_nothing(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    monkeypatch.setenv("STUB_MODE", "no-secret-key")
    result = CliInvoker().invoke(_doctor_app(), ["--format", "json"])  # type: ignore[arg-type]
    [row] = [r for r in json.loads(result.stdout) if r["title"] == "pass token store"]
    assert (row["check"], row["status"]) == ("untaped.token-store", "pass")


def test_contributed_passing_check_reports_detail(_isolated_config: Path) -> None:
    app = _doctor_app(make_plugin("ext", auth=check(detail="token valid")))
    result = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    row = next(r for r in json.loads(result.stdout) if r["check"] == "ext.auth")
    assert row["detail"] == "token valid"


def test_the_columns_table_shows_a_pass_row_detail(_isolated_config: Path) -> None:
    app = _doctor_app(
        make_plugin(
            "ext", auth=check(detail="token valid"), legacy=check(warn=True, detail="old key set")
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
    app = _doctor_app(make_plugin("ext", legacy=check(warn=True, detail="old key set")))
    result = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    row = next(r for r in json.loads(result.stdout) if r["check"] == "ext.legacy")
    assert row["status"] == "warn"
    assert row["detail"] == "old key set"


def test_failed_check_wins_over_warn(_isolated_config: Path) -> None:
    app = _doctor_app(make_plugin("ext", auth=check(ok=False, warn=True, detail="bad")))
    result = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    assert result.exit_code == 1
    row = next(r for r in json.loads(result.stdout) if r["check"] == "ext.auth")
    assert row["status"] == "fail"


# ── execution failures isolate per row ───────────────────────────────────────


def test_failing_check_does_not_block_other_rows(_isolated_config: Path) -> None:
    app = _doctor_app(
        make_plugin(
            "ext", auth=check(ok=False, detail="token rejected"), latency=check(detail="fast")
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

    broken = DoctorCheck(title="t", run=_boom)  # type: ignore[arg-type]
    app = _doctor_app(make_plugin("ext", broken=broken))
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
        make_plugin(
            "ext",
            a=DoctorCheck(title="t", run=_wrong_id),  # type: ignore[arg-type]
            b=DoctorCheck(title="t", run=_not_a_result),  # type: ignore[arg-type]
        )
    )
    result = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert result.exit_code == 1
    assert "ext.a" in result.stdout
    assert "ext.b" in result.stdout


def test_a_result_without_an_id_takes_its_checks(_isolated_config: Path) -> None:
    from untaped.sdk.health.doctor import DoctorCheck, DoctorResult

    def _unnamed(ctx: object) -> DoctorResult:
        return DoctorResult(ok=True, warn=True, detail="worth a look")

    app = _doctor_app(
        make_plugin("ext", a=DoctorCheck(title="t", run=_unnamed))  # type: ignore[arg-type]
    )
    result = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]

    row = next(r for r in json.loads(result.stdout) if r["check"] == "ext.a")
    assert (row["status"], row["detail"]) == ("warn", "worth a look")


def test_a_positional_id_fails_its_row_naming_the_order(_isolated_config: Path) -> None:
    from untaped.sdk.health.doctor import DoctorCheck, DoctorResult

    def _positional(ctx: object) -> DoctorResult:
        return DoctorResult("ext.a", True, "x")  # type: ignore[arg-type, call-arg]

    app = _doctor_app(
        make_plugin("ext", a=DoctorCheck(title="t", run=_positional))  # type: ignore[arg-type]
    )
    result = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]

    row = next(r for r in json.loads(result.stdout) if r["check"] == "ext.a")
    assert row["status"] == "fail"
    assert "id is keyword-only" in row["detail"]


def test_a_detail_that_is_not_a_string_fails_its_row_under_json(_isolated_config: Path) -> None:
    from untaped.sdk.health.doctor import DoctorCheck, DoctorResult

    def _none_detail(ctx: object) -> DoctorResult:
        return DoctorResult(id="ext.a", ok=False, detail=None)  # type: ignore[arg-type]

    def _int_detail(ctx: object) -> DoctorResult:
        return DoctorResult(id="ext.b", ok=True, detail=3)  # type: ignore[arg-type]

    app = _doctor_app(
        make_plugin(
            "ext",
            a=DoctorCheck(title="t", run=_none_detail),  # type: ignore[arg-type]
            b=DoctorCheck(title="t", run=_int_detail),  # type: ignore[arg-type]
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
        "format_version: 2\ngithub:\n  base_url: https://g\njira:\n  timeout: not-a-number\n",
    )
    get_settings.cache_clear()
    app = _doctor_app(
        make_plugin("github", settings=GithubProfile, auth=check(detail="github ok")),
        make_plugin("jira", settings=JiraProfile),
    )
    result = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert result.exit_code == 1
    assert "timeout" in result.stdout
    assert "github.auth" in result.stdout
    assert any("github.auth" in line and "✓" in line for line in result.stdout.splitlines())


def test_missing_required_field_is_a_failed_row(_isolated_config: Path) -> None:
    app = _doctor_app(make_plugin("strict", settings=StrictProfile))
    result = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert result.exit_code == 1
    assert "endpoint" in result.stdout


def test_unparseable_config_fails_config_row_without_blocking_others(
    _isolated_config: Path,
) -> None:
    write_config(_isolated_config, "{ invalid: [yaml\n")
    get_settings.cache_clear()
    app = _doctor_app(
        make_plugin("ext", auth=check(detail="still ran")),
        ghost=True,
    )
    result = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert result.exit_code == 1
    assert "could not parse" in result.stdout
    assert "duplicate-name" in result.stdout
    assert "ext.auth" in result.stdout


# ── quarantine rows fail the run ─────────────────────────────────────────────


def test_quarantine_row_fails_exit(_isolated_config: Path) -> None:
    app = _doctor_app(ghost=True)
    result = CliInvoker().invoke(app, [])  # type: ignore[arg-type]
    assert result.exit_code == 1
    assert "example-dist" in result.stdout
    assert "duplicate-name" in result.stdout
    raw = CliInvoker().invoke(app, ["--format", "raw", "--columns", "detail"])  # type: ignore[arg-type]
    assert raw.exit_code == 1
    assert "duplicate plugin name: 'ghost'" in raw.stdout


#: a plugin whose class can't be imported: composition quarantines it
_BAD = PluginCandidate(distribution="bad-dist", name="bad", target="untaped_bad_nowhere.plugin:Bad")


def test_doctor_reports_a_plugin_that_cannot_load_as_a_quarantine_row() -> None:
    result = bootstrap.compose_root(candidates=[_BAD, plugin_candidate(make_plugin("good"))])
    rows = collect_doctor_rows(result)
    quarantine = [row for row in rows if row["check"] == "quarantine"]
    assert [row["plugin"] for row in quarantine] == ["bad"]
    assert quarantine[0]["status"] == "fail"
    assert str(quarantine[0]["detail"]).endswith(" [distribution bad-dist]")


def test_doctor_names_each_quarantined_plugin(
    broken_first_party_candidates: Callable[[], tuple[PluginCandidate, ...]],
) -> None:
    result = bootstrap.compose_root(candidates=broken_first_party_candidates())
    rows = collect_doctor_rows(result)
    quarantine = [row for row in rows if row["check"] == "quarantine"]
    assert [(row["plugin"], row["title"]) for row in quarantine] == [
        ("awx", "malformed-entry-point"),
        ("jira", "malformed-entry-point"),
    ]
    assert str(quarantine[0]["detail"]).endswith(" [distribution untaped]")


def test_core_s_scope_is_read_as_a_plugin_s() -> None:
    """Core's checks run like a plugin's, only when core is selected; its parts get the rows."""
    result = bootstrap.compose_root(candidates=[])
    core_rows = [row for row in collect_doctor_rows(result) if row["plugin"] == "untaped"]
    checks = {row["check"] for row in core_rows if str(row["check"]).startswith("untaped.")}
    assert "untaped.token-store" in checks
    assert "validate settings" not in [row["title"] for row in core_rows]
    picked = collect_doctor_rows(result, plugins=frozenset({"other"}))
    assert not [row for row in picked if str(row["check"]).startswith("untaped.")]


def test_doctor_cli_exits_1_on_a_quarantined_plugin() -> None:
    root = bootstrap.build_root_app(candidates=[_BAD])
    result = CliInvoker().invoke(root.meta, ["doctor", "--format", "json"])
    assert result.exit_code == 1
    assert any(
        row["check"] == "quarantine" and row["plugin"] == "bad" for row in json.loads(result.stdout)
    )


def test_undefined_active_profile_fails_section_rows(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, "format_version: 2\n")
    monkeypatch.setenv("UNTAPED_PROFILE", "ghost")
    get_settings.cache_clear()
    app = _doctor_app(make_plugin("ext", settings=ExtProfile))
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
        make_plugin("github", settings=GithubProfile),
        make_plugin("ext", auth=check()),
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
    for title in ("validate untaped.http", "validate untaped.ui", "resolve picked profiles"):
        assert title in titles
    assert "validate log_level" not in titles


def test_removed_log_level_is_reported_as_an_unknown_key(_isolated_config: Path) -> None:
    write_config(_isolated_config, "format_version: 2\nlog_level: DEBUG\n")
    code, rows = _rows(_doctor_app())
    assert code == 0
    (row,) = [row for row in rows if row["check"] == "untaped.unknown-keys"]
    assert row["status"] == "warn"
    assert "log_level" in row["detail"]


def test_top_level_keys_no_plugin_reads_are_unknown(_isolated_config: Path) -> None:
    """A top-level ``log_level`` or a section no installed plugin reads is flagged, not read."""
    write_config(
        _isolated_config,
        "format_version: 2\nlog_level: DEBUG\nworkspace:\n  x: 1\n",
    )
    code, rows = _rows(_doctor_app())
    assert code == 0
    (row,) = [row for row in rows if row["check"] == "untaped.unknown-keys"]
    assert row["status"] == "warn"
    assert row["detail"] == "ignored: log_level, workspace"


def test_unknown_ui_theme_fails_ui_row(_isolated_config: Path) -> None:
    write_config(_isolated_config, "format_version: 2\nuntaped:\n  ui:\n    theme: bogus\n")
    code, rows = _rows(_doctor_app())
    assert code == 1
    assert "unknown UI theme" in _failed(rows)["validate untaped.ui"]


def test_a_section_that_is_not_a_mapping_fails_each_core_row(_isolated_config: Path) -> None:
    write_config(_isolated_config, "format_version: 2\nuntaped: 5\n")
    code, rows = _rows(_doctor_app())
    assert code == 1
    failed = _failed(rows)
    for part in ("http", "ui", "skills"):
        assert failed[f"validate untaped.{part}"] == "section 'untaped' must be a mapping"


def test_bad_env_override_fails_its_row_and_names_the_variable(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_UNTAPED__HTTP__TIMEOUT_SECONDS", "abc")
    monkeypatch.setenv("UNTAPED_JIRA__TIMEOUT", "soon")
    code, rows = _rows(_doctor_app(make_plugin("jira", settings=JiraProfile)))
    assert code == 1
    failed = _failed(rows)
    assert "UNTAPED_UNTAPED__HTTP__TIMEOUT_SECONDS" in failed["validate untaped.http"]
    assert "UNTAPED_JIRA__TIMEOUT" in failed["validate settings"]
    assert "validate untaped.ui" not in failed


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
    write_config(_isolated_config, f"format_version: {FORMAT_VERSION}\nprofiles: {{}}\n")
    acme = _acme(items=StateRows[_Row]())
    state_file = _isolated_config.parent / "plugins" / "acme" / "state.yml"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(f"format_version: {STATE_FORMAT_VERSION}\n")
    code, rows = _rows(_doctor_app(acme))
    assert code == 0, _failed(rows)
    (unknown,) = [row for row in rows if row["check"] == "untaped.unknown-keys"]
    assert unknown["status"] == _PASS


def test_active_profile_missing_without_profiles_fails_profile_row(
    _isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_config(_isolated_config, "")
    monkeypatch.setenv("UNTAPED_PROFILE", "prod")
    code, rows = _rows(_doctor_app())
    assert code == 1
    assert "'prod'" in _failed(rows)["resolve picked profiles"]


class _Row(BaseModel):
    name: Annotated[str, Key]
    created_at: str


def _removes(self: Command) -> None:
    return None


def _acme(*, removes: object = None, **blocks: object) -> type[Plugin]:
    """``acme`` holding ``blocks``; ``untaped acme rm`` removes the ``removes`` rows."""
    rm = type("Rm", (Command,), {"__doc__": "Remove an item.", "removes": removes, "run": _removes})
    acme = make_plugin("acme", commands=() if removes is None else (rm,), **blocks)
    assert not compose(acme).quarantine  # its blocks know their plugin, as an installed one's do
    return acme


def test_typed_rows_are_counted_and_a_newer_installs_fields_pass(_isolated_config: Path) -> None:
    items = StateRows[_Row]()
    acme = _acme(items=items, removes=items)
    write_rows(acme.items, [{"name": "a", "created_at": "x", "pinned": True}])  # type: ignore[attr-defined]

    code, rows = _rows(_doctor_app(acme))

    assert code == 0, _failed(rows)
    (state,) = [row for row in rows if row["title"] == "validate state" and row["plugin"] == "acme"]
    assert state["detail"] == "state OK; acme.items: 1 row"


def test_a_damaged_row_fails_with_its_fix(_isolated_config: Path) -> None:
    items = StateRows[_Row]()
    acme = _acme(items=items, removes=items)
    write_rows(acme.items, [{"name": "a"}])  # type: ignore[attr-defined]

    code, rows = _rows(_doctor_app(acme))

    assert code == 1
    state_file = _isolated_config.parent / "plugins" / "acme" / "state.yml"
    assert _failed(rows)["validate state"] == (
        f"acme.items row a in {state_file} is damaged: "
        "created_at: Field required; correct that row by hand, or remove it with "
        "`untaped acme rm a`"
    )


def test_the_current_projects_rows_are_checked(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path / "PROJ-1")
    acme = _acme(here=StateRows[_Row].per_project())
    write_rows(acme.here.of(project), [{"name": "a", "created_at": "x"}, {"name": "b"}])  # type: ignore[attr-defined]

    code, rows = _rows(_doctor_app(acme))
    assert code == 0, _failed(rows)

    monkeypatch.chdir(project.root)
    code, rows = _rows(_doctor_app(acme))
    assert code == 1
    assert "acme.here row b" in _failed(rows)["validate state"]


def test_invalid_state_in_state_file_names_the_file(_isolated_config: Path) -> None:
    acme = _acme(items=StateRows[_Row]())
    state_file = _isolated_config.parent / "plugins" / "acme" / "state.yml"
    state_file.parent.mkdir(parents=True)
    state_file.write_text("items: [not, rows]\n")
    code, rows = _rows(_doctor_app(acme))
    assert code == 1
    assert str(state_file) in _failed(rows)["validate state"]


def test_an_unreadable_state_file_fails_its_row_naming_it(_isolated_config: Path) -> None:
    acme = _acme(items=StateRows[_Row]())
    state_file = _isolated_config.parent / "plugins" / "acme" / "state.yml"
    state_file.parent.mkdir(parents=True)
    state_file.write_text("items: [unclosed\n")
    code, rows = _rows(_doctor_app(acme))
    assert code == 1
    assert _failed(rows)["validate state"].startswith(f"could not parse {state_file}: ")


def test_missing_ca_bundle_fails_http_row(_isolated_config: Path, tmp_path: Path) -> None:
    missing = tmp_path / "nope.pem"
    write_config(
        _isolated_config,
        f"format_version: 2\nuntaped:\n  http:\n    ca_bundle_path: {missing}\n",
    )
    code, rows = _rows(_doctor_app())
    assert code == 1
    assert str(missing) in _failed(rows)["validate untaped.http"]


def test_invalid_ca_bundle_fails_http_row(_isolated_config: Path, tmp_path: Path) -> None:
    bundle = tmp_path / "garbage.pem"
    bundle.write_text("not a certificate")
    write_config(
        _isolated_config,
        f"format_version: 2\nuntaped:\n  http:\n    ca_bundle_path: {bundle}\n",
    )
    code, rows = _rows(_doctor_app())
    assert code == 1
    assert str(bundle) in _failed(rows)["validate untaped.http"]


def test_quiet_keeps_the_checklist_but_mutes_its_footer(_isolated_config: Path) -> None:
    write_config(_isolated_config, "format_version: 2\ngithub:\n  base_url: https://g\n")
    root = bootstrap.build_root_app(
        candidates=(plugin_candidate(make_plugin("github", settings=GithubProfile)),)
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
    acme = _acme(
        items=Cache(label="items", max_age=timedelta(hours=1), model=_Row),
        names=Cache(label="names", max_age=timedelta(hours=1), version=lambda: "2"),
    )
    typed, plain = acme.items, acme.names  # type: ignore[attr-defined]
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
    code, rows = _rows(_doctor_app(acme))

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

    acme = _acme(names=Cache(label="names", max_age=timedelta(hours=1), version=broken))

    code, rows = _rows(_doctor_app(acme))

    assert code == 0, _failed(rows)
    assert _caches_row(rows)["detail"] == "acme.names: could not check its files: no source"


def _folders_row(rows: list[dict[str, str]]) -> dict[str, str]:
    [row] = [row for row in rows if row["check"] == "plugin-folders"]
    return row


def test_an_uninstalled_plugins_folder_warns_with_setup_prune_as_its_fix() -> None:
    folder = named_plugin_dir("gone") / "cached" / "items"
    folder.mkdir(parents=True)
    (folder / "k.json").write_bytes(b"x" * 2048)

    code, rows = _rows(_doctor_app(make_plugin("acme")))

    assert code == 0, _failed(rows)
    assert "not installed" not in _caches_row(rows)["detail"]
    row = _folders_row(rows)
    assert row["status"] == "warn"
    assert row["detail"] == (
        f"{named_plugin_dir('gone')}: plugin 'gone' isn't installed here and nothing here "
        "uses it (2.0 KiB); it goes whole, state included"
    )
    assert row["fix"] == ["--profile", "default", "setup", "prune"]
    assert row["automatic"] is False


def test_a_symlinked_plugin_folder_is_listed_as_kept_and_passes(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    link = named_plugin_dir("gone")
    link.parent.mkdir(parents=True)
    link.symlink_to(shared)

    _, rows = _rows(_doctor_app(make_plugin("acme")))

    row = _folders_row(rows)
    assert row["status"] == _PASS and row["fix"] is None
    assert row["detail"].endswith(f"kept: a symlink to {shared}: it stays")


def test_a_quarantined_plugins_folder_and_a_folder_no_plugin_could_own_are_not_noted() -> None:
    for plugin in ("ghost", ".trash"):
        (named_plugin_dir("acme").parent / plugin / "cached" / "items").mkdir(parents=True)

    _, rows = _rows(_doctor_app(make_plugin("acme"), ghost=True))

    assert _folders_row(rows)["detail"] == "nothing to note"
