"""``untaped doctor fix`` end to end: real ``python -m untaped`` children.

Each first-party automatic fix runs as the subprocess a user's run starts,
under the test plugin's isolated ``HOME``, ``UNTAPED_CONFIG`` and
``UNTAPED_STATE``; the token store is the fake ``pass`` on a narrowed
``PATH``. Core's tests run without any capability package, so a token check
comes from a ``wiz`` capability installed on ``PYTHONPATH`` for the test.
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


_WIZ_MODULE = """
from pydantic import BaseModel, SecretStr

from untaped.cli import create_app
from untaped.sdk import CapabilitySpec, TokenCommand, connection_check


class WizProfile(BaseModel):
    base_url: str | None = None
    token: SecretStr | None = None
    token_command: TokenCommand = None


def provider() -> CapabilitySpec:
    return CapabilitySpec(
        name="wiz",
        app_factory=lambda: create_app(name="wiz", help="wiz capability."),
        config_section="wiz",
        profile_model=WizProfile,
        doctor_checks=(connection_check("wiz.connection", section="wiz"),),
    )
"""


def _install_wiz(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a ``wiz`` capability for this process and the fixes' children."""
    site = tmp_path / "site"
    dist_info = site / "untaped_wiz-1.0.dist-info"
    dist_info.mkdir(parents=True)
    (site / "untaped_wiz.py").write_text(_WIZ_MODULE, encoding="utf-8")
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: untaped-wiz\nVersion: 1.0\n", encoding="utf-8"
    )
    (dist_info / "entry_points.txt").write_text(
        "[untaped.capabilities]\nwiz = untaped_wiz:provider\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.setenv("PYTHONPATH", str(site))


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
    _install_wiz(tmp_path, monkeypatch)
    stores = install_fake_stores(tmp_path, monkeypatch, "pass")
    write_config(
        _isolated_config, "profiles:\n  default:\n    wiz: {base_url: https://wiz, token: s3cret}\n"
    )
    assert _status("wiz.connection") == "warn"
    result = _untaped("doctor", "fix", "--yes", "--format", "json")
    assert result.exit_code == 0, result.output
    [row] = _rows(result)
    assert (row["fix"][2:], row["checks"], row["action"]) == (
        ["auth", "migrate"],
        ["wiz.connection"],
        "fixed",
    )
    assert row["detail"].startswith("1 ")
    assert "token" not in read_config_dict(_isolated_config)["profiles"]["default"]["wiz"]
    assert stores.entries() == {"untaped/default/wiz": "s3cret"}
    assert _status("wiz.connection") == "pass"
    assert "s3cret" not in result.output
