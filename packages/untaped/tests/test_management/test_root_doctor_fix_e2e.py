"""``untaped doctor fix`` end to end: real ``python -m untaped`` children.

Each first-party automatic fix runs as the subprocess a user's run starts,
under the test plugin's isolated ``HOME``, ``UNTAPED_CONFIG`` and
``UNTAPED_STATE``; the token store is the fake ``pass`` on a narrowed
``PATH``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from test_management.stores import install_fake_stores
from test_management.support import write_config
from untaped import bootstrap
from untaped.config_file import read_config_dict
from untaped.testing import CliResult, invoke_cli

pytestmark = pytest.mark.usefixtures("_isolated_config")


def _untaped(*args: str) -> CliResult:
    return invoke_cli(bootstrap.build_root_app().meta, list(args))


def _rows(result: CliResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(result.stdout)
    return rows


def _status(check: str, title: str | None = None) -> str:
    rows = _rows(_untaped("doctor", "--format", "json"))
    return next(
        str(row["status"])
        for row in rows
        if row["check"] == check and title in (None, row["title"])
    )


def test_skills_update_fixes_an_outdated_installed_skill(_isolated_config: Path) -> None:
    installed = _untaped("skills", "install", "untaped", "--target", "claude")
    assert installed.exit_code == 0, installed.output
    skill = Path.home() / ".claude" / "skills" / "untaped" / "SKILL.md"
    skill.write_text("stale\n", encoding="utf-8")
    assert _status("skills") == "warn"
    result = _untaped("doctor", "fix", "--yes", "--format", "json")
    assert result.exit_code == 0, result.output
    [row] = _rows(result)
    assert (row["fix"][2:], row["checks"], row["action"]) == (
        ["skills", "update"],
        ["skills"],
        "fixed",
    )
    assert _status("skills") == "pass"


def test_auth_migrate_moves_a_plaintext_token(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "pass")
    write_config(
        _isolated_config, "profiles:\n  default:\n    awx: {base_url: https://awx, token: s3cret}\n"
    )
    assert _status("awx.connection") == "warn"
    result = _untaped("doctor", "fix", "--yes", "--format", "json")
    assert result.exit_code == 0, result.output
    [row] = _rows(result)
    assert (row["fix"][2:], row["checks"], row["action"]) == (
        ["auth", "migrate"],
        ["awx.connection"],
        "fixed",
    )
    assert row["detail"].startswith("1 ")
    assert "token" not in read_config_dict(_isolated_config)["profiles"]["default"]["awx"]
    assert stores.entries() == {"untaped/default/awx": "s3cret"}
    assert _status("awx.connection") == "pass"
    assert "s3cret" not in result.output
