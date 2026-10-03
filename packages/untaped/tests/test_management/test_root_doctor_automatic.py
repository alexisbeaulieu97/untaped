"""``automatic`` on doctor checks and rows.

A check marks a fix that needs no value and no input ``automatic``; doctor
rows carry the flag in every structured format and fail a check that
declares an automatic fix it cannot have.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from test_management.support import (
    FAIL,
    WizProfile,
    asset,
    compose,
    make_spec,
    wiz_api_check,
    write_config,
)
from untaped import bootstrap
from untaped.capabilities.registry import (
    CapabilityContext,
    CapabilitySpec,
    DoctorCheck,
    DoctorResult,
)
from untaped.management.doctor import build_root_doctor_app
from untaped.sdk import connection_check
from untaped.skills import SkillInstallScope, SkillInstallTarget, install_skills
from untaped.testing import CliInvoker, CliResult

pytestmark = pytest.mark.usefixtures("_isolated_config")


def _doctor(*specs: CapabilitySpec, args: tuple[str, ...] = ("--format", "json")) -> CliResult:
    app = build_root_doctor_app(shell=bootstrap.SHELL_SPEC, result=compose(*specs))
    return CliInvoker().invoke(app, list(args))


def _rows(*specs: CapabilitySpec, online: bool = False) -> list[dict[str, Any]]:
    args = ("--format", "json", "--online") if online else ("--format", "json")
    rows: list[dict[str, Any]] = json.loads(_doctor(*specs, args=args).stdout)
    return rows


def _row(rows: list[dict[str, Any]], check: str) -> dict[str, Any]:
    return next(row for row in rows if row["check"] == check)


def _declaring(
    *, ok: bool = False, warn: bool = False, fix: str | list[str] | None
) -> CapabilitySpec:
    def run(_ctx: CapabilityContext) -> DoctorResult:
        return DoctorResult(
            id="svc.check", ok=ok, warn=warn, detail="found it", fix=fix, automatic=True
        )

    check = DoctorCheck(id="svc.check", title="svc check", run=run)
    return make_spec("svc", doctor_checks=(check,))


def test_a_result_is_manual_by_default() -> None:
    assert DoctorResult(id="x", ok=False, detail="d", fix="skills update").automatic is False


def test_a_plaintext_token_fix_is_automatic(_isolated_config: Path) -> None:
    write_config(
        _isolated_config, "profiles:\n  default:\n    wiz: {base_url: https://w, token: t}\n"
    )
    wiz = make_spec(
        "wiz",
        profile_model=WizProfile,
        doctor_checks=(connection_check("wiz.connection", section="wiz"),),
    )
    row = _row(_rows(wiz), "wiz.connection")
    assert (row["status"], row["automatic"]) == ("warn", True)
    assert row["fix"] == ["--profile", "default", "auth", "migrate"]


def test_an_online_fix_is_manual(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    wiz: {base_url: https://w, token_command: [x]}\n",
    )
    FAIL.append(True)
    try:
        wiz = make_spec("wiz", profile_model=WizProfile, doctor_checks=(wiz_api_check(),))
        row = _row(_rows(wiz, online=True), "wiz.api")
    finally:
        FAIL.clear()
    assert (row["status"], row["fix"][2:], row["automatic"]) == (
        "fail",
        ["auth", "set", "wiz"],
        False,
    )


def test_the_skills_update_fix_is_automatic(tmp_path: Path) -> None:
    skill = asset(tmp_path, "untaped-demo")
    install_skills(
        {skill.name: skill},
        [skill.name],
        stdin=False,
        all_skills=False,
        target=SkillInstallTarget.claude,
        force=False,
        scope=SkillInstallScope.global_,
        project_dir=None,
        target_dir=None,
    )
    skill.source.joinpath("SKILL.md").write_text("changed\n", encoding="utf-8")
    row = _row(_rows(make_spec("demo", skills=(skill,))), "skills")
    assert (row["status"], row["automatic"]) == ("warn", True)


def test_every_structured_format_carries_automatic() -> None:
    spec = _declaring(warn=True, ok=True, fix="skills update")
    as_json = _row(_rows(spec), "svc.check")
    assert as_json["automatic"] is True
    as_yaml = yaml.safe_load(_doctor(spec, args=("--format", "yaml")).stdout)
    assert _row(as_yaml, "svc.check")["automatic"] is True
    pipe = _doctor(spec, args=("--format", "pipe")).stdout.splitlines()
    records = [json.loads(line)["record"] for line in pipe]
    assert _row(records, "svc.check")["automatic"] is True


def test_a_row_without_a_fix_is_never_automatic() -> None:
    rows = _rows(make_spec("svc"))
    assert {row["automatic"] for row in rows} == {False}
    assert _row(rows, "config")["status"] == "pass"


@pytest.mark.parametrize(
    ("fix", "detail"),
    [
        (None, "check svc.check declares an automatic fix but no fix"),
        (
            "config set svc.base_url <URL>",
            "check svc.check declares an automatic fix that needs <URL>",
        ),
        (
            ["config", "set", "svc.<KEY>", "<VALUE>"],
            "check svc.check declares an automatic fix that needs <KEY>, <VALUE>",
        ),
    ],
)
def test_an_impossible_automatic_fix_fails_its_row(
    fix: str | list[str] | None, detail: str
) -> None:
    other = make_spec("other", doctor_checks=())
    result = _doctor(_declaring(fix=fix), other)
    assert result.exit_code == 1
    rows: list[dict[str, Any]] = json.loads(result.stdout)
    row = _row(rows, "svc.check")
    assert (row["status"], row["detail"], row["fix"], row["automatic"]) == (
        "fail",
        detail,
        None,
        False,
    )
    # Isolation: the other rows still render.
    assert any(r["capability"] == "other" for r in rows)


def test_a_passing_row_still_validates_its_automatic_fix() -> None:
    row = _row(json.loads(_doctor(_declaring(ok=True, fix="auth set <NAME>")).stdout), "svc.check")
    assert row["status"] == "fail"
    assert row["detail"] == "check svc.check declares an automatic fix that needs <NAME>"


def test_a_passing_row_with_a_valid_automatic_fix_passes() -> None:
    row = _row(_rows(_declaring(ok=True, fix="skills update")), "svc.check")
    assert (row["status"], row["fix"], row["automatic"]) == ("pass", None, False)
