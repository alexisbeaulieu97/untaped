"""``online_probe_rows``: one plugin's online checks against candidate values.

``setup`` calls it before saving. It runs only the plugin's ``online=True``
checks, over the loaded config with the candidate values laid on top
(a child run's overlay), and never writes anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import HttpUrl, SecretStr

from core_test_support.commands.stores import FakeStores, install_fake_stores
from core_test_support.commands.support import (
    FAIL,
    PROBES,
    WIZ_TOKEN,
    WizProfile,
    WizServer,
    compose,
    make_spec,
    wiz_probe,
    write_config,
)
from untaped.core.commands.doctor import online_probe_rows
from untaped.runtime.registry import CompositionResult
from untaped.sdk import online_check
from untaped.sdk._app_context import app_context
from untaped.sdk._run import current_overlay
from untaped.sdk.errors import ConfigError
from untaped.sdk.health.doctor import DoctorCheck, DoctorResult
from untaped.sdk.testing import current_memory_store

pytestmark = pytest.mark.usefixtures("_isolated_config", "home_run")

_OLD = "format_version: 2\nwiz:\n  server: {url: https://old}\n"

#: What each probe saw: ``{"url", "source", "plain", "overlay"}``.
SEEN: list[dict[str, Any]] = []
OFFLINE_CALLS: list[str] = []


def _seeing_probe() -> str:
    server = app_context().section("wiz", WizProfile).server
    token = None if server is None else WIZ_TOKEN.find(server)
    # A command's token is never read here: reading it would run the command.
    command = token is not None and "command" in token.source
    SEEN.append(
        {
            "url": None if server is None else str(server.url),
            "source": None if token is None else token.source,
            "plain": None if token is None or command else token.get_secret_value(),
            "overlay": current_overlay() is not None,
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
    write_config(_isolated_config, _OLD)

    rows = _rows({"server.url": "https://new", "token": SecretStr("typed")})

    assert [row["check"] for row in rows] == ["wiz.api"]
    assert OFFLINE_CALLS == []
    assert rows[0]["status"] == "pass"
    assert rows[0]["detail"] == "authenticated as alice"


@pytest.mark.parametrize("stored", [False, True])
def test_probe_sees_candidate_url_and_token(_isolated_config: Path, stored: bool) -> None:
    write_config(
        _isolated_config,
        "format_version: 2\nwiz:\n  server: {url: https://new}\n  token: file\n",
    )
    if stored:
        WIZ_TOKEN.store(WizServer(url=HttpUrl("https://new")), token="stored")

    rows = _rows({"server.url": "https://new", "token": SecretStr("typed")})

    assert rows[0]["status"] == "pass"
    # The candidate answers before the stored login and the file's old token.
    assert SEEN == [
        {
            "url": "https://new/",
            "source": "setup's candidate",
            "plain": "typed",
            "overlay": True,
        }
    ]


def test_candidate_command_source_removes_the_token(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "format_version: 2\nwiz:\n  server: {url: https://old}\n  token: file\n",
    )

    rows = _rows(
        {"server.url": "https://new", "token": None, "token_command": ["op", "read", "x"]},
    )

    assert rows[0]["status"] == "pass"
    # The command source, not the file's token, is what resolves (it is not run here).
    assert SEEN[0]["source"] == "setup's wiz token command"


def test_a_new_profile_is_checked_over_default_before_it_exists(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WIZ_TOKEN", "shared")
    write_config(_isolated_config, "format_version: 2\nwiz: {}\n")

    rows = _rows({"server.url": "https://prod"}, profile="prod")

    assert rows[0]["status"] == "pass"
    assert (SEEN[0]["url"], SEEN[0]["plain"]) == ("https://prod/", "shared")
    assert "prod" not in _isolated_config.read_text()


def test_a_typed_candidate_token_runs_only_the_online_check(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(_isolated_config, "format_version: 2\n")

    rows = _rows({"server.url": "https://new", "token": SecretStr("typed")})

    assert {row["check"] for row in rows} == {"wiz.api"}  # no offline row
    assert capsys.readouterr().err == ""


def test_invalid_candidate_values_fail_the_validate_row(_isolated_config: Path) -> None:
    write_config(_isolated_config, "format_version: 2\n")

    rows = _rows({"server.url": "not a url"})

    assert [(row["check"], row["status"], row["title"]) for row in rows] == [
        ("settings", "fail", "validate settings")
    ]
    assert "server.url" in str(rows[0]["detail"])
    assert SEEN == []


def test_the_files_are_untouched(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores: FakeStores = install_fake_stores(tmp_path, monkeypatch, "pass")
    write_config(_isolated_config, _OLD)
    before = _isolated_config.read_bytes()

    _rows(
        {"server.url": "https://new", "token": SecretStr("typed"), "token_command": ["pass", "x"]},
    )

    assert _isolated_config.read_bytes() == before
    assert not _isolated_config.with_name("state.yml").exists()
    assert stores.entries() == {} and stores.calls() == []
    assert current_memory_store().entries == {}


def test_a_failed_probe_row_carries_detail_and_fix(_isolated_config: Path) -> None:
    write_config(_isolated_config, "format_version: 2\n")
    FAIL.append(True)

    rows = _rows(
        {"server.url": "https://new", "token": SecretStr("bad")},
        result=_composition(probe=wiz_probe),
    )

    assert rows[0]["status"] == "fail"
    assert "401" in str(rows[0]["detail"])
    assert rows[0]["fix"] == ["--profile", "default", "auth", "login", "wiz"]


def test_a_plugin_that_is_not_composed_is_an_error(_isolated_config: Path) -> None:
    with pytest.raises(ConfigError, match="nope"):
        online_probe_rows(_composition(), "default", "nope", {})
