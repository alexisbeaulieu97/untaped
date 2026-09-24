"""Offline doctor rows: config permissions, unknown keys, outdated skills,
and the shared ``connection_check``/``executable_check`` factories."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, SecretStr

from test_management.support import GithubProfile, asset, compose, make_spec, write_config
from untaped import bootstrap
from untaped.capability_api import (
    TokenCommand,
    TokenSources,
    connection_check,
    executable_check,
)
from untaped.management.doctor import build_root_doctor_app
from untaped.skills import SkillInstallScope, SkillInstallTarget, install_skills
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config")


class ApiProfile(BaseModel):
    """Token-bearing profile double (section ``api``)."""

    token_sources: ClassVar[TokenSources] = TokenSources(env=("API_TOKEN",))

    base_url: str | None = None
    token: SecretStr | None = None
    token_command: TokenCommand = None


def _rows(*specs: Any) -> list[dict[str, Any]]:
    app = build_root_doctor_app(shell=bootstrap.SHELL_SPEC, result=compose(*specs))
    result = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    rows: list[dict[str, Any]] = json.loads(result.stdout)
    return rows


def _row(rows: list[dict[str, Any]], check: str, title: str | None = None) -> dict[str, Any]:
    return next(r for r in rows if r["check"] == check and title in (None, r["title"]))


def test_group_readable_config_file_warns(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default: {}\n")
    _isolated_config.chmod(0o644)
    row = _row(_rows(), "config", "config file permissions")
    assert row["status"] == "warn"
    assert "chmod 600" in row["detail"]
    _isolated_config.chmod(0o600)
    assert _row(_rows(), "config", "config file permissions")["status"] == "pass"


def test_unknown_keys_warn_with_their_full_path(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    github:\n      tokn: x\n      base_url: https://g\n"
        "    http:\n      timout: 3\n    ui:\n      symbols: {ok: y}\n"
        "  work:\n    nope: 1\n",
    )
    row = _row(_rows(make_spec("github", profile_model=GithubProfile)), "unknown-keys")
    assert row["status"] == "warn"
    assert row["detail"] == (
        "ignored: profiles.default.github.tokn, profiles.default.http.timout, profiles.work.nope"
    )


def test_known_keys_pass(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    http:\n      timeout: 3\n")
    assert _row(_rows(), "unknown-keys")["status"] == "pass"


def test_outdated_installed_skill_warns(tmp_path: Path) -> None:
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
    spec = make_spec("demo", skills=(skill,))
    assert _row(_rows(spec), "skills")["status"] == "pass"
    skill.source.joinpath("SKILL.md").write_text("changed\n", encoding="utf-8")
    row = _row(_rows(spec), "skills")
    assert row["status"] == "warn"
    assert "update with `untaped skills update`" in row["detail"]


def test_skill_no_longer_shipped_warns(tmp_path: Path) -> None:
    skill = asset(tmp_path, "untaped-gone")
    install_skills(
        {skill.name: skill},
        [skill.name],
        stdin=False,
        all_skills=False,
        target=SkillInstallTarget.codex,
        force=False,
        scope=SkillInstallScope.global_,
        project_dir=None,
        target_dir=None,
    )
    row = _row(_rows(make_spec("demo")), "skills")
    assert row["status"] == "warn"
    assert "orphaned:" in row["detail"]
    assert "untaped skills remove" in row["detail"]


@pytest.mark.parametrize(
    ("config", "env", "status", "detail"),
    [
        ("{}", {}, "pass", "not configured"),
        ("{base_url: https://a}", {}, "warn", "api.token not configured"),
        ("{token: t}", {}, "warn", "api.base_url not configured"),
        ("{base_url: https://a, token: t}", {}, "pass", "https://a; token from api.token"),
        (
            "{base_url: https://a, token_command: [x]}",
            {},
            "pass",
            "token from api.token_command",
        ),
        ("{base_url: https://a}", {"API_TOKEN": "e"}, "pass", "token from $API_TOKEN"),
    ],
)
def test_connection_check(
    _isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: str,
    env: dict[str, str],
    status: str,
    detail: str,
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    write_config(_isolated_config, f"profiles:\n  default:\n    api: {config}\n")
    spec = make_spec(
        "api",
        profile_model=ApiProfile,
        doctor_checks=(connection_check("api.connection", section="api"),),
    )
    row = _row(_rows(spec), "api.connection")
    assert row["status"] == status
    assert detail in row["detail"]


def test_executable_check_warns_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    checks = (
        executable_check("ext.present", "present-tool", purpose="present things"),
        executable_check("ext.absent", "absent-tool", purpose="absent things"),
    )
    real_which = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, *a, **k: "/bin/present-tool" if name == "present-tool" else real_which(name),
    )
    rows = _rows(make_spec("ext", doctor_checks=checks))
    assert _row(rows, "ext.present")["status"] == "pass"
    absent = _row(rows, "ext.absent")
    assert absent["status"] == "warn"
    assert absent["detail"] == "`absent-tool` not found on PATH; absent things will fail"
