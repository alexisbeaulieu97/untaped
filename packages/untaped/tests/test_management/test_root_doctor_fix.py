"""``untaped doctor fix``: select, classify, confirm, run, re-check, report.

The child process is the one seam (``fix._run_one``); a stub records each
argv and "heals" the checks it names, so the re-check sees what a real fix
would leave behind. Checks name real root commands of a test composition.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from test_management.support import make_spec, write_config
from untaped import bootstrap
from untaped.capabilities.registry import (
    CapabilityContext,
    CapabilitySpec,
    DoctorCheck,
    DoctorResult,
)
from untaped.management import fix
from untaped.management.fix import ChildRun
from untaped.profile_resolver import profile_scope
from untaped.testing import CliResult, ScriptedPromptBackend, invoke_cli, provider_candidate

pytestmark = pytest.mark.usefixtures("_isolated_config")

#: Check ids whose problem is gone (a stubbed fix healed them).
HEALED: set[str] = set()
#: Check ids that keep warning after their fix ran.
STUBBORN: set[str] = set()
#: Every argv the stubbed runner received.
RAN: list[list[str]] = []


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    HEALED.clear()
    STUBBORN.clear()
    RAN.clear()
    yield


def _check(
    check_id: str,
    fix_: str | None,
    *,
    automatic: bool = True,
    fail: bool = False,
    online: bool = False,
) -> DoctorCheck:
    def run(_ctx: CapabilityContext) -> DoctorResult:
        if check_id in HEALED and check_id not in STUBBORN:
            return DoctorResult(id=check_id, ok=True, detail="healthy")
        return DoctorResult(
            id=check_id,
            ok=not fail,
            warn=not fail,
            detail=f"{check_id} is off",
            fix=fix_,
            automatic=automatic and fix_ is not None,
        )

    return DoctorCheck(id=check_id, title=f"{check_id} title", run=run, online=online)


def _stub(
    results: dict[str, ChildRun] | None = None,
) -> Callable[[tuple[str, ...]], ChildRun]:
    """A ``_run_one`` that heals every check covered by the argv it runs."""

    def run(argv: tuple[str, ...]) -> ChildRun:
        RAN.append(list(argv))
        command = " ".join(argv[2:])
        for check_id, fix_ in _FIXES.items():
            if fix_ == command:
                HEALED.add(check_id)
        default = ChildRun(code=0, rows=[{"action": "moved"}, {"action": "moved"}], diagnostics=[])
        return (results or {}).get(command, default)

    return run


_FIXES: dict[str, str] = {}


def _spec(*checks: tuple[str, str | None, dict[str, Any]]) -> CapabilitySpec:
    _FIXES.clear()
    built = []
    for check_id, fix_, options in checks:
        if fix_ is not None:
            _FIXES[check_id] = fix_
        built.append(_check(check_id, fix_, **options))
    return make_spec("svc", doctor_checks=tuple(built))


def _cli(
    spec: CapabilitySpec,
    *args: str,
    monkeypatch: pytest.MonkeyPatch,
    results: dict[str, ChildRun] | None = None,
    **kwargs: Any,
) -> CliResult:
    monkeypatch.setattr(fix, "_run_one", _stub(results))
    root = bootstrap.build_root_app(candidates=(provider_candidate(spec),))
    return invoke_cli(root.meta, ["doctor", "fix", *args], **kwargs)


def _rows(result: CliResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(result.stdout)
    return rows


_MIGRATE = ("svc.a", "auth migrate", {})
_UPDATE = ("svc.b", "skills update", {})


# ── selection and classification ─────────────────────────────────────────────


def test_rows_sharing_a_fix_make_one_outcome_listing_both_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _spec(_MIGRATE, ("svc.c", "auth migrate", {}), _UPDATE)
    result = _cli(spec, "--yes", "--format", "json", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    rows = _rows(result)
    assert [row["checks"] for row in rows] == [["svc.a", "svc.c"], ["svc.b"]]
    assert RAN == [
        ["--profile", "default", "auth", "migrate"],
        ["--profile", "default", "skills", "update"],
    ]


def test_manual_fixes_are_skipped_with_what_they_need(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _spec(
        ("svc.url", "config set svc.base_url <URL>", {"automatic": False}),
        ("svc.token", "auth set svc", {"automatic": False}),
    )
    result = _cli(spec, "--yes", "--format", "json", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    assert [(row["action"], row["detail"]) for row in _rows(result)] == [
        ("skipped", "needs <URL>; run it yourself"),
        ("skipped", "asks for input; run it yourself"),
    ]
    assert RAN == []


def test_a_fix_running_doctor_or_an_unknown_command_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _spec(("svc.loop", "doctor --online", {}), ("svc.typo", "nope run", {}))
    result = _cli(spec, "--yes", "--format", "json", monkeypatch=monkeypatch)
    assert result.exit_code == 1
    rows = _rows(result)
    assert [(row["action"], row["detail"]) for row in rows] == [
        ("failed", "a fix cannot run doctor"),
        ("failed", "command not found: 'nope'"),
    ]
    assert rows[0]["error"]["category"] == "failed"
    assert RAN == []


@pytest.mark.parametrize(
    "argv",
    [
        ("--profile", "p", "skills", "update"),
        ("--profile=p", "skills", "update"),
        ("skills", "update"),
    ],
)
def test_the_command_is_read_past_either_profile_spelling(argv: tuple[str, ...]) -> None:
    assert fix._command(argv) == ["skills", "update"]


def test_a_lazily_mounted_capability_command_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _spec(("svc.own", "svc repair", {}))
    result = _cli(spec, "--yes", "--format", "json", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    assert RAN == [["--profile", "default", "svc", "repair"]]


# ── outcomes ─────────────────────────────────────────────────────────────────


def test_a_fix_that_heals_its_rows_is_fixed_with_the_childs_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _cli(_spec(_MIGRATE), "--yes", "--format", "json", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    assert _rows(result) == [
        {
            "fix": ["--profile", "default", "auth", "migrate"],
            "checks": ["svc.a"],
            "action": "fixed",
            "detail": "2 moved",
        }
    ]
    assert result.stderr.splitlines()[-1] == '{"level": "info", "message": "doctor fix: 1 fixed"}'


def test_a_row_still_warning_makes_the_fix_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    STUBBORN.add("svc.a")
    result = _cli(_spec(_MIGRATE), "--yes", "--format", "json", monkeypatch=monkeypatch)
    assert result.exit_code == 1
    [row] = _rows(result)
    assert (row["action"], row["detail"]) == ("partial", "2 moved; still warns: svc.a is off")


def test_a_failed_fix_carries_the_childs_error_and_the_next_one_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = {
        "level": "error",
        "category": "auth",
        "system": "awx",
        "retryable": False,
        "message": "token rejected",
        "hint": "run `untaped auth set awx`",
        "exit_code": 4,
        "details": {},
    }
    failed = ChildRun(code=4, rows=[], diagnostics=[{"level": "info", "message": "x"}, error])
    spec = _spec(_MIGRATE, _UPDATE)
    result = _cli(
        spec, "--yes", "--format", "json", monkeypatch=monkeypatch, results={"auth migrate": failed}
    )
    assert result.exit_code == 4
    rows = _rows(result)
    assert [row["action"] for row in rows] == ["failed", "fixed"]
    assert rows[0]["detail"] == "token rejected; run `untaped auth set awx`"
    assert rows[0]["error"] == {
        "category": "auth",
        "system": "awx",
        "retryable": False,
        "message": "token rejected",
        "hint": "run `untaped auth set awx`",
    }
    assert "error" not in rows[1]
    assert "error: untaped auth migrate" not in result.stderr


def test_a_fix_that_cannot_start_fails_and_the_next_one_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "executable", str(tmp_path / "missing-python"))
    spec = _spec(_MIGRATE, _UPDATE)
    root = bootstrap.build_root_app(candidates=(provider_candidate(spec),))
    result = invoke_cli(root.meta, ["doctor", "fix", "--yes", "--format", "json"])
    assert result.exit_code == 1
    rows = _rows(result)
    assert [row["action"] for row in rows] == ["failed", "failed"]
    assert all(row["detail"].startswith("could not start untaped: ") for row in rows)
    assert "Traceback" not in result.stderr


def test_a_child_failing_without_an_error_line_reads_its_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = ChildRun(code=2, rows=[], diagnostics=[{"level": "error", "category": "bogus"}])
    result = _cli(
        _spec(_MIGRATE),
        "--yes",
        "--format",
        "json",
        monkeypatch=monkeypatch,
        results={"auth migrate": failed},
    )
    assert result.exit_code == 1
    [row] = _rows(result)
    assert row["detail"] == "exited with status 2"


@pytest.mark.parametrize(
    ("child", "detail"),
    [
        (
            ChildRun(code=0, rows=[], diagnostics=[{"level": "success", "message": "updated 2"}]),
            "updated 2",
        ),
        (
            ChildRun(code=0, rows=[{"name": "x"}], diagnostics=[{"level": "info", "message": "i"}]),
            "done",
        ),
    ],
)
def test_without_outcome_rows_the_detail_falls_back(
    monkeypatch: pytest.MonkeyPatch, child: ChildRun, detail: str
) -> None:
    result = _cli(
        _spec(_MIGRATE),
        "--yes",
        "--format",
        "json",
        monkeypatch=monkeypatch,
        results={"auth migrate": child},
    )
    assert _rows(result)[0]["detail"] == detail


def test_rows_are_matched_by_capability_check_and_title(monkeypatch: pytest.MonkeyPatch) -> None:
    covered = {"rows": [("svc", "svc.a", "svc.a title")]}
    rows = [
        {"capability": "svc", "check": "svc.a", "title": "other title", "status": "warn"},
        {"capability": "svc", "check": "svc.a", "title": "svc.a title", "status": "pass"},
    ]
    one = fix._Fix(argv=("x",), keys=tuple(covered["rows"]), checks=("svc.a",), automatic=True)
    run = ChildRun(code=0, rows=[], diagnostics=[])
    assert fix._outcome(one, None, run, rows, dry_run=False)["action"] == "fixed"  # type: ignore[arg-type]


# ── modes ────────────────────────────────────────────────────────────────────


def test_dry_run_plans_automatic_fixes_and_runs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _spec(_MIGRATE, ("svc.url", "config set svc.base_url <URL>", {"automatic": False}))
    result = _cli(spec, "--dry-run", "--format", "json", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    assert [(row["action"], row["detail"]) for row in _rows(result)] == [
        ("planned", "would fix svc.a"),
        ("skipped", "needs <URL>; run it yourself"),
    ]
    assert RAN == []


def test_a_declined_prompt_runs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = ScriptedPromptBackend(confirms=[False])
    spec = _spec(_MIGRATE, ("svc.token", "auth set svc", {"automatic": False}))
    result = _cli(spec, monkeypatch=monkeypatch, terminal=True, prompt_backend=backend)
    assert result.exit_code == 1
    assert result.stderr.endswith("cancelled; no changes made\n")
    assert "Fixes to run (1):\n  → untaped auth migrate  svc.a\n" in result.stderr
    assert "Needs you (1):\n  → untaped auth set svc  svc.token\n" in result.stderr
    assert backend.calls == [("confirm", "Continue?")]
    assert RAN == []


def test_without_a_terminal_it_needs_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _cli(_spec(_MIGRATE), monkeypatch=monkeypatch)
    assert result.exit_code == 2
    assert RAN == []


def test_yes_prints_no_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _cli(_spec(_MIGRATE), "--yes", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    assert "Fixes to run" not in result.stderr


def test_online_reaches_the_collection_and_the_recheck(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    online = _check("svc.api", "auth migrate", online=True)

    def counted(ctx: CapabilityContext) -> DoctorResult:
        calls.append("probe")
        return online.run(ctx)

    _FIXES.clear()
    _FIXES["svc.api"] = "auth migrate"
    spec = make_spec(
        "svc", doctor_checks=(DoctorCheck("svc.api", "svc API", counted, online=True),)
    )
    offline = _cli(spec, "--yes", "--format", "json", monkeypatch=monkeypatch)
    assert (offline.stdout, calls) == ("[]\n", [])
    result = _cli(spec, "--yes", "--online", "--format", "json", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    assert _rows(result)[0]["action"] == "fixed"
    assert calls == ["probe", "probe"]


def test_quiet_keeps_the_plan_and_rows_but_mutes_nothing_to_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = ScriptedPromptBackend(confirms=[True])
    spec = _spec(_MIGRATE)
    monkeypatch.setattr(fix, "_run_one", _stub())
    root = bootstrap.build_root_app(candidates=(provider_candidate(spec),))
    result = invoke_cli(
        root.meta, ["--quiet", "doctor", "fix"], terminal=True, prompt_backend=backend
    )
    assert result.exit_code == 0, result.output
    assert "Fixes to run (1):" in result.stderr
    assert "fixed" in result.stdout
    quiet = invoke_cli(root.meta, ["--quiet", "doctor", "fix"])
    assert "nothing to fix" not in quiet.stderr
    loud = invoke_cli(root.meta, ["doctor", "fix"])
    assert (loud.stdout, loud.stderr) == ("", "nothing to fix\n")


# ── exit codes ───────────────────────────────────────────────────────────────


def test_nothing_to_fix_with_a_failing_check_exits_1_and_points_at_doctor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _cli(_spec(("svc.bad", None, {"fail": True})), "--yes", monkeypatch=monkeypatch)
    assert result.exit_code == 1
    assert "nothing to fix" in result.stderr
    assert result.stderr.splitlines()[-1] == "hint: run `untaped doctor`"


def test_a_check_still_failing_after_the_run_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _spec(
        _MIGRATE, ("svc.url", "config set svc.base_url <URL>", {"fail": True, "automatic": False})
    )
    result = _cli(spec, "--yes", "--format", "json", monkeypatch=monkeypatch)
    assert result.exit_code == 1
    assert [row["action"] for row in _rows(result)] == ["fixed", "skipped"]
    assert result.stderr.splitlines()[-1] == '{"level": "hint", "message": "run `untaped doctor`"}'


def test_nothing_to_fix_and_healthy_exits_0(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _cli(_spec(), "--format", "json", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    assert result.stdout == "[]\n"


# ── human view ───────────────────────────────────────────────────────────────


def test_the_result_is_one_line_per_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    STUBBORN.add("svc.b")
    spec = _spec(_MIGRATE, _UPDATE, ("svc.token", "auth set svc", {"automatic": False}))
    result = _cli(spec, "--yes", monkeypatch=monkeypatch)
    assert result.stdout.splitlines() == [
        "  ✓ untaped auth migrate   fixed    2 moved",
        "  ◐ untaped skills update  partial  2 moved; still warns: svc.b is off",
        "  ○ untaped auth set svc   skipped  asks for input; run it yourself",
    ]
    assert "doctor fix: 1 fixed, 1 partial, 1 skipped" in result.stderr


def test_the_result_keeps_a_profile_the_flag_chose(monkeypatch: pytest.MonkeyPatch) -> None:
    with profile_scope("default"):
        result = _cli(_spec(_MIGRATE), "--yes", monkeypatch=monkeypatch)
    assert "untaped --profile default auth migrate" in result.stdout


def test_columns_prints_the_table_with_the_command_line(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _cli(_spec(_MIGRATE), "--yes", "--columns", "fix,action", monkeypatch=monkeypatch)
    cells = [c.strip() for c in result.stdout.splitlines()[3].strip("│").split("│")]
    assert cells == ["untaped auth migrate", "fixed"]


def test_an_ascii_theme_swaps_the_glyphs(
    monkeypatch: pytest.MonkeyPatch, _isolated_config: Path
) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    ui: {theme: plain}\n")
    STUBBORN.add("svc.b")
    spec = _spec(_MIGRATE, _UPDATE)
    backend = ScriptedPromptBackend(confirms=[True])
    result = _cli(spec, monkeypatch=monkeypatch, terminal=True, prompt_backend=backend)
    assert [line.split()[0] for line in result.stdout.splitlines()] == ["+", "~"]
    assert "  -> untaped auth migrate" in result.stderr


# ── the hint on doctor and setup ─────────────────────────────────────────────


def _doctor(spec: CapabilitySpec, *root_args: str) -> CliResult:
    root = bootstrap.build_root_app(candidates=(provider_candidate(spec),))
    return invoke_cli(root.meta, [*root_args, "doctor"])


def test_doctor_hints_at_doctor_fix_counting_unique_fixes() -> None:
    spec = _spec(_MIGRATE, ("svc.c", "auth migrate", {}), _UPDATE)
    assert _doctor(spec).stderr.splitlines()[-1] == (
        "hint: run `untaped doctor fix` to apply 2 automatic fixes"
    )


def test_the_hint_names_the_fixes_that_need_you() -> None:
    spec = _spec(
        _MIGRATE,
        ("svc.token", "auth set svc", {"automatic": False}),
        ("svc.url", "config set svc.base_url <URL>", {"automatic": False}),
    )
    assert _doctor(spec).stderr.splitlines()[-1] == (
        "hint: run `untaped doctor fix` to apply 1 automatic fix; 2 fixes need you"
    )
    one = _spec(_MIGRATE, ("svc.token", "auth set svc", {"automatic": False}))
    assert _doctor(one).stderr.splitlines()[-1].endswith("; 1 fix needs you")


def test_the_hint_keeps_a_profile_the_flag_chose(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default: {}\n  work: {}\n")
    stderr = _doctor(_spec(_MIGRATE), "--profile", "work").stderr
    assert "hint: run `untaped --profile work doctor fix`" in stderr


def _stale_skill() -> None:
    root = bootstrap.build_root_app()
    installed = invoke_cli(root.meta, ["skills", "install", "untaped", "--target", "claude"])
    assert installed.exit_code == 0, installed.output
    skill = Path.home() / ".claude" / "skills" / "untaped" / "SKILL.md"
    skill.write_text("stale\n", encoding="utf-8")


def _old_http_key() -> None:
    write_config(
        Path(os.environ["UNTAPED_CONFIG"]), "profiles:\n  default:\n    http:\n      timeout: 9\n"
    )


#: Puts each shell row that can name a fix into its fixable state, by check id.
_SHELL_FIX_TRIGGERS: dict[str, Callable[[], None]] = {
    "skills": _stale_skill,
    "deprecated-keys": _old_http_key,
}


def test_every_shell_fix_row_has_a_check_id_of_its_own() -> None:
    """``doctor fix`` re-checks a shell fix row by its check id, so no other shell row shares it."""
    for trigger in _SHELL_FIX_TRIGGERS.values():
        trigger()
    root = bootstrap.build_root_app()
    rows = json.loads(invoke_cli(root.meta, ["doctor", "--format", "json"]).stdout)
    shell = [row for row in rows if row["capability"] == bootstrap.SHELL_SPEC.name]
    fixable = {row["check"] for row in shell if row["fix"]}
    assert fixable == set(_SHELL_FIX_TRIGGERS)
    for check in fixable:
        assert [row["check"] for row in shell].count(check) == 1, check


def test_doctor_fix_runs_config_migrate_for_an_old_key(_isolated_config: Path) -> None:
    """End to end: the real child renames the key, and the re-check passes."""
    _old_http_key()
    root = bootstrap.build_root_app()

    result = invoke_cli(root.meta, ["doctor", "fix", "--yes", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {
            "fix": ["--profile", "default", "config", "migrate"],
            "checks": ["deprecated-keys"],
            "action": "fixed",
            "detail": "1 renamed",
        }
    ]
    assert _isolated_config.read_text(encoding="utf-8") == (
        "profiles:\n  default:\n    http:\n      timeout_seconds: 9\n"
    )


def test_no_automatic_fix_no_hint() -> None:
    stderr = _doctor(_spec(("svc.token", "auth set svc", {"automatic": False}))).stderr
    assert "doctor fix" not in stderr


# ── the runner ───────────────────────────────────────────────────────────────


_CHILD = """
import json, os, sys
print(json.dumps([{"argv": sys.argv[1:], "stdin": sys.stdin.read(),
    "diagnostics": os.environ.get("UNTAPED_DIAGNOSTICS"),
    "format": os.environ.get("UNTAPED_FORMAT")}]))
print(json.dumps({"level": "success", "message": "child done"}), file=sys.stderr)
"""


@pytest.fixture
def fake_python(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``sys.executable`` becomes a script that reports how it was run."""
    script = tmp_path / "python"
    script.write_text(f"#!{sys.executable}\n{_CHILD}", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(script))
    monkeypatch.setenv("UNTAPED_FORMAT", "yaml")


@pytest.mark.usefixtures("fake_python")
def test_the_child_gets_json_closed_stdin_and_no_format_variable() -> None:
    run = fix._run_one(("--profile", "p", "auth", "migrate"))
    assert run.code == 0
    [seen] = run.rows
    assert seen == {
        "argv": ["-m", "untaped", "--profile", "p", "auth", "migrate", "--format", "json"],
        "stdin": "",
        "diagnostics": "json",
        "format": None,
    }
    assert run.diagnostics == [{"level": "success", "message": "child done"}]


@pytest.mark.usefixtures("fake_python")
@pytest.mark.parametrize("flag", [["--format", "yaml"], ["--format=yaml"], ["-f", "yaml"]])
def test_a_named_format_is_not_doubled(flag: list[str]) -> None:
    run = fix._run_one(("skills", "update", *flag))
    assert run.rows[0]["argv"] == ["-m", "untaped", "skills", "update", *flag]


@pytest.mark.usefixtures("fake_python")
def test_verbose_relays_the_childs_streams(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(fix, "is_verbose", lambda: True)
    fix._run_one(("skills", "update"))
    assert "child done" in capfd.readouterr().err


def test_unparsable_output_has_no_rows() -> None:
    assert fix._parse_rows("not json") == []
    assert fix._parse_rows('{"action": "x"}') == [{"action": "x"}]
    assert fix._parse_lines(["oops\n", '"s"\n', '{"level": "info"}\n']) == [{"level": "info"}]
