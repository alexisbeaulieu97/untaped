"""The checklist: the human view of ``doctor`` and ``setup`` rows.

Rows grouped by capability in row order, one glyph per status (ASCII under
an ASCII theme), each fix under its row as the command line to type, and a
footer counting every status. ``--columns`` prints the table instead.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rich.cells import cell_len

from test_management.support import check, compose, make_spec, write_config
from untaped import bootstrap
from untaped.capabilities.registry import (
    CapabilityContext,
    CapabilitySpec,
    DoctorCheck,
    DoctorResult,
)
from untaped.management._render import _UNICODE_GLYPHS
from untaped.management.doctor import build_root_doctor_app, report_check_rows
from untaped.profile_resolver import profile_scope
from untaped.testing import CliInvoker, CliResult

pytestmark = pytest.mark.usefixtures("_isolated_config")


@pytest.fixture(autouse=True)
def _wide(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wide enough that no line wraps, whatever terminal runs the tests."""
    monkeypatch.setenv("COLUMNS", "500")


def _fixing(check_id: str, fix: str, *, automatic: bool, ok: bool = False) -> DoctorCheck:
    def run(_ctx: CapabilityContext) -> DoctorResult:
        return DoctorResult(
            id=check_id, ok=ok, warn=ok, detail=f"{check_id} broke", fix=fix, automatic=automatic
        )

    return DoctorCheck(id=check_id, title=f"{check_id} title", run=run)


def _specs() -> tuple[CapabilitySpec, ...]:
    return (
        make_spec(
            "alpha",
            doctor_checks=(
                check("alpha.ok", detail="all fine", title="alpha ok"),
                _fixing("alpha.auto", "skills update", automatic=True, ok=True),
            ),
        ),
        make_spec(
            "beta", doctor_checks=(_fixing("beta.manual", "auth set beta", automatic=False),)
        ),
    )


def _doctor(*args: str) -> CliResult:
    app = build_root_doctor_app(
        shell=bootstrap.SHELL_SPEC, builtin_for=lambda _name: None, result=compose(*_specs())
    )
    return CliInvoker().invoke(app, list(args))


def test_rows_are_grouped_by_capability_in_row_order() -> None:
    lines = _doctor().stdout.splitlines()
    groups = [line for line in lines if line and not line.startswith(" ")]
    assert groups == ["untaped", "alpha", "beta"]
    alpha = lines.index("alpha")
    assert lines[alpha + 1].split()[1] == "settings"
    assert [line.split()[1] for line in lines[alpha + 2 : alpha + 4]] == ["alpha.ok", "alpha.auto"]


def test_each_row_has_a_glyph_and_shows_its_detail() -> None:
    stdout = _doctor().stdout
    ok = next(line for line in stdout.splitlines() if "alpha.ok" in line)
    assert ok.split()[0] == "✓"
    assert ok.endswith("all fine")
    auto = next(line for line in stdout.splitlines() if " alpha.auto " in line)
    assert auto.split()[0] == "▲"
    manual = next(line for line in stdout.splitlines() if " beta.manual " in line)
    assert manual.split()[0] == "✗"


def test_a_fix_shows_under_its_row_tagged_when_automatic() -> None:
    lines = _doctor().stdout.splitlines()
    auto = next(i for i, line in enumerate(lines) if " alpha.auto " in line)
    assert lines[auto + 1].strip() == "→ untaped skills update  (automatic)"
    manual = next(i for i, line in enumerate(lines) if " beta.manual " in line)
    assert lines[manual + 1].strip() == "→ untaped auth set beta"
    # The fix line sits under the title column.
    assert lines[auto + 1].index("→") == lines[auto].index("alpha.auto title")


def test_a_fix_keeps_a_profile_the_flag_chose() -> None:
    with profile_scope("default"):
        stdout = _doctor().stdout
    assert "→ untaped --profile default auth set beta\n" in stdout


def test_the_footer_counts_every_status_and_a_failure_exits_1() -> None:
    result = _doctor()
    assert result.exit_code == 1
    footer = next(line for line in result.stderr.splitlines() if line.startswith("doctor: "))
    assert footer.endswith(" pass, 1 warn, 1 fail")


def test_a_healthy_run_prints_its_footer_too() -> None:
    app = build_root_doctor_app(
        shell=bootstrap.SHELL_SPEC, builtin_for=lambda _name: None, result=compose()
    )
    result = CliInvoker().invoke(app, [])
    assert result.exit_code == 0, result.output
    assert result.stderr.splitlines()[-1].endswith(" pass")


def test_the_footer_names_the_operation(capsys: pytest.CaptureFixture[str]) -> None:
    row = {
        "check": "x",
        "capability": "untaped",
        "status": "warn",
        "title": "t",
        "detail": "d",
        "fix": None,
        "automatic": False,
    }
    report_check_rows([row], op="setup", fmt="table", columns=None)
    assert capsys.readouterr().err.splitlines()[-1] == "setup: 1 warn"


def test_an_ascii_theme_swaps_every_glyph(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    ui: {theme: plain}\n")
    stdout = _doctor().stdout
    glyphs = {line.split()[0] for line in stdout.splitlines() if line.startswith("  ")}
    assert glyphs == {"+", "!", "x", "->"}


def test_a_broken_ui_section_still_renders(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    ui: {theme: nope}\n")
    result = _doctor()
    assert result.exit_code == 1
    ui = next(line for line in result.stdout.splitlines() if "validate ui" in line)
    assert ui.split()[0] == "✗"
    assert "unknown UI theme" in ui


def test_columns_prints_the_table_with_the_fix_as_a_command_line() -> None:
    result = _doctor("--columns", "check,status,detail,fix")
    lines = result.stdout.splitlines()
    header = [cell.strip() for cell in lines[1].strip("│").split("│")]
    assert header == ["check", "status", "detail", "fix"]
    auto = next(line for line in lines if " alpha.auto " in line)
    cells = [cell.strip() for cell in auto.strip("│").split("│")]
    assert cells == ["alpha.auto", "warn", "alpha.auto broke", "untaped skills update"]
    assert any("alpha.ok" in line and "all fine" in line for line in lines)
    assert "; run" not in result.stdout


def test_structured_rows_keep_their_shape_and_the_footer_is_a_json_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    result = _doctor("--format", "json")
    row = next(row for row in json.loads(result.stdout) if row["check"] == "alpha.auto")
    assert row == {
        "check": "alpha.auto",
        "capability": "alpha",
        "status": "warn",
        "title": "alpha.auto title",
        "detail": "alpha.auto broke",
        "fix": ["--profile", "default", "skills", "update"],
        "automatic": True,
    }
    lines = [json.loads(line) for line in result.stderr.splitlines()]
    assert lines[-2]["message"].startswith("doctor: ")
    assert lines[-1] == {
        "level": "hint",
        "message": "run `untaped doctor fix` to apply 1 automatic fix; 1 fix needs you",
    }


@pytest.mark.parametrize("columns", ["80", "40"])
def test_a_long_detail_wraps_inside_its_group(
    monkeypatch: pytest.MonkeyPatch, columns: str
) -> None:
    monkeypatch.setenv("COLUMNS", columns)
    spec = make_spec(
        "alpha",
        doctor_checks=(
            check("alpha.long", warn=True, detail="a detail " * 12, title="alpha long"),
        ),
    )
    app = build_root_doctor_app(
        shell=bootstrap.SHELL_SPEC, builtin_for=lambda _name: None, result=compose(spec)
    )
    lines = CliInvoker().invoke(app, []).stdout.splitlines()
    groups = [line for line in lines if line and not line.startswith(" ")]
    assert groups == ["untaped", "alpha"]
    assert all(len(line) <= int(columns) for line in lines)
    start = lines.index("alpha")
    detail = " ".join(line.strip() for line in lines[start + 2 :])
    assert detail.endswith(("a detail " * 12).strip())


def test_glyphs_take_the_status_colours(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    stdout = _doctor().stdout
    assert "\x1b[33m▲" in stdout
    assert "\x1b[31m✗" in stdout
    assert "\x1b[32m✓" in stdout


#: Glyphs that no terminal renders as an emoji; U+26A0 ``⚠`` (and ``✔``, ``⚡``…)
#: have an emoji form that terminals draw two cells wide and misalign rows.
_TEXT_ONLY_GLYPHS = set("✓✗▲◐○→")


def test_status_glyphs_are_one_cell_and_never_emoji() -> None:
    for status, glyph in _UNICODE_GLYPHS.items():
        assert len(glyph) == 1, status
        assert cell_len(glyph) == 1, status
        assert glyph in _TEXT_ONLY_GLYPHS, f"{status}: {glyph!r} may render as a wide emoji"
