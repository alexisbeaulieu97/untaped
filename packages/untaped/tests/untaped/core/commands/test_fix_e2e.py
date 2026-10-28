"""``untaped doctor fix`` end to end: real ``python -m untaped`` children.

Each first-party automatic fix runs as the subprocess a user's run starts,
under the test plugin's isolated ``HOME`` (and so its own untaped home).
Core's tests run without any plugin package, so a renamed setting comes
from a ``wiz`` plugin installed on ``PYTHONPATH`` for the test.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core_test_support.commands.support import age_stub, write_config
from untaped.runtime import bootstrap
from untaped.runtime.config_file import read_config_dict
from untaped.sdk.testing import CliResult, invoke_cli

pytestmark = pytest.mark.usefixtures("_isolated_config")


_WIZ_MODULE = """
from typing import ClassVar

from pydantic import BaseModel

from untaped.sdk._cli import create_app
from untaped.sdk import PluginSpec


class WizProfile(BaseModel):
    timeout_seconds: float | None = None

    renamed_keys: ClassVar[dict[str, str]] = {"timeout": "timeout_seconds"}


SPEC = PluginSpec(
    name="wiz",
    app_factory=lambda: create_app(name="wiz", help="wiz plugin."),
    settings=WizProfile,
)
"""


def _install_wiz(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a ``wiz`` plugin for this process and the fixes' children."""
    site = tmp_path / "site"
    dist_info = site / "untaped_wiz-1.0.dist-info"
    dist_info.mkdir(parents=True)
    (site / "untaped_wiz.py").write_text(_WIZ_MODULE, encoding="utf-8")
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: untaped-wiz\nVersion: 1.0\n", encoding="utf-8"
    )
    (dist_info / "entry_points.txt").write_text(
        "[untaped.plugins]\nwiz = untaped_wiz:SPEC\n", encoding="utf-8"
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
    age_stub(Path.home() / ".claude" / "skills" / "untaped")
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


def test_config_migrate_renames_an_old_key(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_wiz(tmp_path, monkeypatch)
    write_config(_isolated_config, "format_version: 2\nwiz: {timeout: 9}\n")
    assert _status("deprecated-keys") == "warn"
    result = _untaped("doctor", "fix", "--yes", "--format", "json")
    assert result.exit_code == 0, result.output
    [row] = _rows(result)
    assert (row["fix"], row["checks"], row["action"], row["detail"]) == (
        ["--profile", "default", "config", "migrate"],
        ["deprecated-keys"],
        "fixed",
        "1 renamed",
    )
    assert read_config_dict(_isolated_config)["wiz"] == {"timeout_seconds": 9}
    assert _status("deprecated-keys") == "pass"
