"""``online_probe_rows``: one plugin's online checks against candidate values.

``setup`` calls it before saving. It runs only the plugin's ``online=True``
checks, over the loaded config with the candidate values laid on top
(``settings_overlay``), and never writes anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from test_management.stores import FakeStores, install_fake_stores
from test_management.support import (
    FAIL,
    PROBES,
    WizProfile,
    compose,
    make_spec,
    wiz_probe,
    write_config,
)
from untaped.app_context import app_context
from untaped.doctor_checks import connection_check
from untaped.errors import ConfigError
from untaped.management.doctor import online_probe_rows
from untaped.plugins.registry import CompositionResult, DoctorCheck, DoctorResult
from untaped.sdk import online_check
from untaped.settings import active_overlay

pytestmark = pytest.mark.usefixtures("_isolated_config")

#: What each probe saw of its section: ``{"base_url", "token", "token_type", "command"}``.
SEEN: list[dict[str, Any]] = []
OFFLINE_CALLS: list[str] = []


def _seeing_probe() -> str:
    section = app_context().section("wiz", WizProfile)
    token = section.token
    SEEN.append(
        {
            "base_url": section.base_url,
            "token": None if token is None else type(token).__name__,
            "plain": token.get_secret_value() if type(token) is SecretStr else None,
            "command": section.token_command,
            "overlay": active_overlay() is not None,
        }
    )
    return "authenticated as alice"


def _offline_check() -> DoctorCheck:
    def run(_ctx: object) -> DoctorResult:
        OFFLINE_CALLS.append("ran")
        return DoctorResult(id="wiz.offline", ok=True, detail="offline")

    return DoctorCheck(id="wiz.offline", title="wiz offline", run=run)  # type: ignore[arg-type]


def _composition(*extra: DoctorCheck, probe: Any = _seeing_probe) -> CompositionResult:
    checks = (
        connection_check("wiz.connection", section="wiz"),
        _offline_check(),
        online_check("wiz.api", section="wiz", probe=probe),
        *extra,
    )
    return compose(make_spec("wiz", settings=WizProfile, doctor_checks=checks))


@pytest.fixture(autouse=True)
def _reset() -> None:
    PROBES.clear()
    FAIL.clear()
    SEEN.clear()
    OFFLINE_CALLS.clear()


def _rows(
    values: dict[str, object],
    *,
    profile: str = "default",
    result: CompositionResult | None = None,
) -> list[dict[str, object]]:
    return online_probe_rows(result or _composition(), profile, "wiz", values)


def test_only_online_checks_run(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    wiz:\n      base_url: https://old\n")

    rows = _rows({"base_url": "https://new"})

    assert [row["check"] for row in rows] == ["wiz.api"]
    assert OFFLINE_CALLS == []
    assert rows[0]["status"] == "pass"
    assert rows[0]["detail"] == "authenticated as alice"


def test_probe_sees_candidate_base_url_and_token(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    wiz:\n      base_url: https://old\n      token: file\n",
    )

    rows = _rows({"base_url": "https://new", "token": SecretStr("typed")})

    assert rows[0]["status"] == "pass"
    assert SEEN == [
        {
            "base_url": "https://new",
            "token": "SecretStr",
            "plain": "typed",
            "command": None,
            "overlay": True,
        }
    ]


def test_candidate_command_source_removes_the_token(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    wiz:\n      base_url: https://old\n      token: file\n",
    )

    rows = _rows(
        {"base_url": "https://new", "token": None, "token_command": ["op", "read", "x"]},
    )

    assert rows[0]["status"] == "pass"
    # The command source, not the file's token, is what resolves (it is not run here).
    assert SEEN[0]["token"] == "CommandToken"
    assert SEEN[0]["command"] == ["op", "read", "x"]


def test_a_new_profile_is_checked_over_default_before_it_exists(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    wiz:\n      token: shared\n")

    rows = _rows({"base_url": "https://prod"}, profile="prod")

    assert rows[0]["status"] == "pass"
    assert (SEEN[0]["base_url"], SEEN[0]["plain"]) == ("https://prod", "shared")
    assert "prod" not in _isolated_config.read_text()


def test_a_plaintext_candidate_token_does_not_report_plaintext(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(_isolated_config, "profiles:\n  default: {}\n")

    rows = _rows({"base_url": "https://new", "token": SecretStr("typed")})

    assert {row["check"] for row in rows} == {"wiz.api"}  # no connection-settings row
    assert capsys.readouterr().err == ""


def test_invalid_candidate_values_fail_the_validate_row(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default: {}\n")

    rows = _rows({"token_command": ["", "x"]})

    assert [(row["check"], row["status"], row["title"]) for row in rows] == [
        ("settings", "fail", "validate settings")
    ]
    assert "token_command" in str(rows[0]["detail"])
    assert SEEN == []


def test_the_files_are_untouched(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores: FakeStores = install_fake_stores(tmp_path, monkeypatch, "pass")
    write_config(_isolated_config, "profiles:\n  default:\n    wiz:\n      base_url: https://old\n")
    before = _isolated_config.read_bytes()

    _rows(
        {"base_url": "https://new", "token": SecretStr("typed"), "token_command": ["pass", "x"]},
    )

    assert _isolated_config.read_bytes() == before
    assert not _isolated_config.with_name("state.yml").exists()
    assert stores.entries() == {} and stores.calls() == []


def test_a_failed_probe_row_carries_detail_and_fix(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default: {}\n")
    FAIL.append(True)

    rows = _rows(
        {"base_url": "https://new", "token": SecretStr("bad")},
        result=_composition(probe=wiz_probe),
    )

    assert rows[0]["status"] == "fail"
    assert "401" in str(rows[0]["detail"])
    assert rows[0]["fix"] == ["--profile", "default", "auth", "set", "wiz"]


def test_a_plugin_that_is_not_composed_is_an_error(_isolated_config: Path) -> None:
    with pytest.raises(ConfigError, match="nope"):
        online_probe_rows(_composition(), "default", "nope", {})
