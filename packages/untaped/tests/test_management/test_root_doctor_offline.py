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
from untaped.management.config import build_root_config_app
from untaped.management.doctor import build_root_doctor_app
from untaped.sdk import (
    TokenCommand,
    TokenSources,
    connection_check,
    executable_check,
)
from untaped.skills import SkillInstallScope, SkillInstallTarget, install_skills
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config")


class ApiProfile(BaseModel):
    """Token-bearing profile double (section ``api``)."""

    token_sources: ClassVar[TokenSources] = TokenSources(env=("API_TOKEN",))

    base_url: str | None = None
    token: SecretStr | None = None
    token_command: TokenCommand = None


def _rows(*specs: Any, exit_code: int = 0) -> list[dict[str, Any]]:
    app = build_root_doctor_app(
        shell=bootstrap.SHELL_SPEC, builtin_for=lambda _name: None, result=compose(*specs)
    )
    result = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    assert result.exit_code == exit_code, result.output
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
        "    http:\n      timout: 3\n    ui:\n      symbols: {success: y}\n"
        "  work:\n    nope: 1\n",
    )
    row = _row(_rows(make_spec("github", settings=GithubProfile)), "unknown-keys")
    assert row["status"] == "warn"
    assert row["detail"] == (
        "ignored: profiles.default.github.tokn, profiles.default.http.timout, profiles.work.nope"
    )


def test_an_unknown_ui_symbol_name_fails_the_ui_row(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    ui:\n      symbols: {zzz: y}\n")
    row = _row(_rows(exit_code=1), "settings", "validate ui")
    assert row["status"] == "fail"
    assert "ui.symbols.zzz" in row["detail"]
    assert "success" in row["detail"]


def test_a_stray_ui_name_loads_leniently_and_only_fails_the_ui_row(
    _isolated_config: Path,
) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    http:\n      timeout_seconds: 3\n"
        "    ui:\n      symbols: {zzz: y}\n",
    )
    listed = CliInvoker().invoke(
        build_root_config_app(shell=bootstrap.SHELL_SPEC, result=compose()), ["list"]
    )
    assert listed.exit_code == 0, listed.output
    rows = _rows(exit_code=1)
    assert _row(rows, "settings", "validate ui")["status"] == "fail"
    assert _row(rows, "settings", "validate http")["status"] == "pass"
    assert [r["title"] for r in rows if r["status"] == "fail"] == ["validate ui"]


def test_an_unknown_ui_color_role_name_fails_the_ui_row(_isolated_config: Path) -> None:
    write_config(
        _isolated_config, "profiles:\n  default:\n    ui:\n      color_roles: {zzz: red}\n"
    )
    row = _row(_rows(exit_code=1), "settings", "validate ui")
    assert row["status"] == "fail"
    assert "ui.color_roles.zzz" in row["detail"]


def test_a_declared_ui_symbol_name_passes_the_ui_row(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    ui:\n      symbols: {success: y}\n")
    assert _row(_rows(), "settings", "validate ui")["status"] == "pass"


def test_known_keys_pass(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    http:\n      timeout_seconds: 3\n")
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
    assert row["fix"] == ["--profile", "default", "skills", "update"]
    assert row["automatic"] is True


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
    # Orphaned skills alone have no fix, so nothing is automatic.
    assert (row["fix"], row["automatic"]) == (None, False)


@pytest.mark.parametrize(
    ("config", "env", "status", "detail"),
    [
        ("{}", {}, "pass", "not configured"),
        ("{}", {"API_TOKEN": "e"}, "pass", "not configured"),
        ("{base_url: https://a}", {}, "warn", "api.token not configured"),
        ("{token: t}", {}, "warn", "api.base_url not configured"),
        (
            "{base_url: https://a, token: t}",
            {},
            "warn",
            "https://a; api.token is stored in plain text in config.yml",
        ),
        (
            "{base_url: https://a, token: t}",
            {"UNTAPED_API__TOKEN": "o"},
            "warn",
            "https://a; api.token is stored in plain text in config.yml",
        ),
        ("{base_url: https://a}", {"UNTAPED_API__TOKEN": "o"}, "pass", "token from api.token"),
        ("{base_url: https://a}", {"untaped_api__token": "o"}, "pass", "token from api.token"),
        (
            "{base_url: https://a}",
            {"UNTAPED_API": '{"token": "o"}'},
            "pass",
            "token from api.token",
        ),
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
        settings=ApiProfile,
        doctor_checks=(connection_check("api.connection", section="api"),),
    )
    row = _row(_rows(spec), "api.connection")
    assert row["status"] == status
    assert detail in row["detail"]


class BareProfile(BaseModel):
    """Token-bearing profile double with no ``token_command`` or env fallbacks."""

    base_url: str | None = None
    token: SecretStr | None = None


def test_plaintext_token_without_fallbacks_points_at_the_untaped_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "work.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(config))
    write_config(config, "profiles:\n  default:\n    bare: {base_url: https://b, token: t}\n")
    spec = make_spec(
        "bare",
        settings=BareProfile,
        doctor_checks=(connection_check("bare.connection", section="bare"),),
    )
    row = _row(_rows(spec), "bare.connection")
    assert row["status"] == "warn"
    assert row["detail"] == (
        "https://b; bare.token is stored in plain text in work.yml; "
        "use $UNTAPED_BARE__TOKEN instead"
    )


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
