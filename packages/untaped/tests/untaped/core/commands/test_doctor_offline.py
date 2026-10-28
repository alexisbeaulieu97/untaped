"""Offline doctor rows: config permissions, unknown keys, outdated skills,
and the shared ``ExecutableCheck`` block."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from core_test_support.commands.support import (
    GithubProfile,
    age_stub,
    compose,
    config_root,
    make_plugin,
    root_group,
    write_config,
)
from untaped.core.commands.skills import composed_skills
from untaped.core.skills import SkillInstallScope, SkillInstallTarget, install_skills
from untaped.sdk import ExecutableCheck
from untaped.sdk.storage._refs import RefHolder
from untaped.sdk.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config", "home_run")


def _rows(*specs: Any, exit_code: int = 0) -> list[dict[str, Any]]:
    app = root_group("doctor", *specs)
    result = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    assert result.exit_code == exit_code, result.output
    rows: list[dict[str, Any]] = json.loads(result.stdout)
    return rows


def _row(rows: list[dict[str, Any]], check: str, title: str | None = None) -> dict[str, Any]:
    return next(r for r in rows if r["check"] == check and title in (None, r["title"]))


def test_group_readable_config_file_warns(_isolated_config: Path) -> None:
    write_config(_isolated_config, "format_version: 2\n")
    _isolated_config.chmod(0o644)
    row = _row(_rows(), "untaped.config-permissions")
    assert row["status"] == "warn"
    assert "chmod 600" in row["detail"]
    _isolated_config.chmod(0o600)
    assert _row(_rows(), "untaped.config-permissions")["status"] == "pass"


def test_unknown_keys_warn_with_their_full_path(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "format_version: 2\ngithub:\n  tokn: x\n  base_url: https://g\n"
        "untaped:\n  http:\n    timout: 3\n  ui:\n    symbols: {success: y}\n"
        "profiles:\n  work:\n    nope: 1\n",
    )
    row = _row(_rows(make_plugin("github", settings=GithubProfile)), "untaped.unknown-keys")
    assert row["status"] == "warn"
    assert row["detail"] == ("ignored: github.tokn, untaped.http.timout, profiles.work.nope")


def test_unknown_keys_name_the_other_registered_installs(
    _isolated_config: Path, tmp_path: Path
) -> None:
    from untaped.sdk.testing import write_reference

    write_config(_isolated_config, "format_version: 2\nacme: {parallel: 4}\n")
    other = tmp_path / "checkout"
    (other / "untaped-11.1.0.dist-info").mkdir(parents=True)
    write_reference(
        RefHolder(kind="install", name="aaaaaaaaaaaa"), [], anchor=other, version="11.1.0"
    )
    row = _row(_rows(), "untaped.unknown-keys")
    assert row["detail"] == (
        f"ignored: acme (another untaped here may read them: untaped 11.1.0 at {other})"
    )


def test_an_unknown_ui_symbol_name_fails_the_ui_row(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "format_version: 2\nuntaped:\n  ui:\n    symbols: {zzz: y}\n",
    )
    row = _row(_rows(exit_code=1), "settings", "validate untaped.ui")
    assert row["status"] == "fail"
    assert "untaped.ui.symbols.zzz" in row["detail"]
    assert "success" in row["detail"]


def test_a_stray_ui_name_loads_leniently_and_only_fails_the_ui_row(
    _isolated_config: Path,
) -> None:
    write_config(
        _isolated_config,
        "format_version: 2\nuntaped:\n  http:\n    timeout_seconds: 3\n"
        "untaped:\n  ui:\n    symbols: {zzz: y}\n",
    )
    listed = CliInvoker().invoke(config_root(), ["list"])
    assert listed.exit_code == 0, listed.output
    rows = _rows(exit_code=1)
    assert _row(rows, "settings", "validate untaped.ui")["status"] == "fail"
    assert _row(rows, "settings", "validate untaped.http")["status"] == "pass"
    assert [r["title"] for r in rows if r["status"] == "fail"] == ["validate untaped.ui"]


def test_an_unknown_ui_color_role_name_fails_the_ui_row(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "format_version: 2\nuntaped:\n  ui:\n    color_roles: {zzz: red}\n",
    )
    row = _row(_rows(exit_code=1), "settings", "validate untaped.ui")
    assert row["status"] == "fail"
    assert "untaped.ui.color_roles.zzz" in row["detail"]


def test_a_declared_ui_symbol_name_passes_the_ui_row(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "format_version: 2\nuntaped:\n  ui:\n    symbols: {success: y}\n",
    )
    assert _row(_rows(), "settings", "validate untaped.ui")["status"] == "pass"


def test_known_keys_pass(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "format_version: 2\nuntaped:\n  http:\n    timeout_seconds: 3\n",
    )
    assert _row(_rows(), "untaped.unknown-keys")["status"] == "pass"


def test_outdated_installed_skill_warns() -> None:
    spec = make_plugin("demo", skill="Teach agents how to use untaped-demo.")
    install_skills(
        composed_skills(compose(spec)),
        ["untaped-demo"],
        all_skills=False,
        target=SkillInstallTarget.claude,
        force=False,
        scope=SkillInstallScope.global_,
        project_dir=None,
        target_dir=None,
    )
    assert _row(_rows(spec), "skills")["status"] == "pass"
    age_stub(Path.home() / ".claude" / "skills" / "untaped-demo")
    row = _row(_rows(spec), "skills")
    assert row["status"] == "warn"
    assert row["fix"] == ["--profile", "default", "skills", "update"]
    assert row["automatic"] is True


def test_skill_no_longer_shipped_warns() -> None:
    spec = make_plugin("gone", skill="Teach agents how to use untaped-gone.")
    install_skills(
        composed_skills(compose(spec)),
        ["untaped-gone"],
        all_skills=False,
        target=SkillInstallTarget.codex,
        force=False,
        scope=SkillInstallScope.global_,
        project_dir=None,
        target_dir=None,
    )
    row = _row(_rows(make_plugin("demo")), "skills")
    assert row["status"] == "warn"
    assert "orphaned skill:" in row["detail"]
    assert "no plugin installed here" in row["detail"]
    assert "untaped skills remove" in row["detail"]
    # Orphaned skills alone have no fix, so nothing is automatic.
    assert (row["fix"], row["automatic"]) == (None, False)


def test_executable_check_warns_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    real_which = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, *a, **k: "/bin/present-tool" if name == "present-tool" else real_which(name),
    )
    rows = _rows(
        make_plugin(
            "ext",
            present=ExecutableCheck("present-tool", purpose="present things"),
            absent=ExecutableCheck("absent-tool", purpose="absent things"),
        )
    )
    assert _row(rows, "ext.present")["status"] == "pass"
    absent = _row(rows, "ext.absent")
    assert absent["status"] == "warn"
    assert absent["detail"] == "`absent-tool` not found on PATH; absent things will fail"


def test_core_s_own_checks_are_rows_of_its_class(_isolated_config: Path) -> None:
    write_config(_isolated_config, "format_version: 2\n")
    checks = [r["check"] for r in _rows() if r["check"].startswith("untaped.")]
    assert checks == ["untaped.config-permissions", "untaped.unknown-keys", "untaped.token-store"]
