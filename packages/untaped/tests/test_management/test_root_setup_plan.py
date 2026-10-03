"""``untaped setup plan``: a profile's remaining setup steps as rows an agent acts on.

Every ``run`` is a complete argv (``--profile`` first, ``<NAME>``
placeholders), every step that handles a secret is ``by: user``, and the
online rows are the capabilities' own online doctor checks.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, SecretStr

from test_management.stores import install_fake_stores
from test_management.support import make_spec, write_config
from untaped import bootstrap
from untaped.capabilities.registry import CapabilitySpec
from untaped.sdk import (
    HttpStatusError,
    TokenCommand,
    TokenSources,
    connection_check,
    online_check,
)
from untaped.testing import CliResult, invoke_cli, provider_candidate

pytestmark = pytest.mark.usefixtures("_isolated_config")


class WizProfile(BaseModel):
    """Service double (section ``wiz``)."""

    token_sources: ClassVar[TokenSources] = TokenSources(env=("WIZ_TOKEN",))

    base_url: str | None = None
    token: SecretStr | None = None
    token_command: TokenCommand = None


class HubProfile(BaseModel):
    """GitHub-like double: a default URL and ``GH_TOKEN`` (section ``hub``)."""

    token_sources: ClassVar[TokenSources] = TokenSources(env=("GH_TOKEN",))

    base_url: str = "https://hub.example"
    token: SecretStr | None = None
    token_command: TokenCommand = None


class LegacyProfile(BaseModel):
    """Service double without ``token_command`` (section ``legacy``)."""

    base_url: str | None = None
    token: SecretStr | None = None


_PROBES: list[str] = []
_FAIL: list[bool] = []


def _probe() -> str:
    _PROBES.append("probed")
    if _FAIL:
        raise HttpStatusError("HTTP 401 from https://wiz/me", status_code=401)
    return "authenticated as alice"


@pytest.fixture(autouse=True)
def _clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No store, no ``gh`` and no ambient token unless a test adds one."""
    _PROBES.clear()
    _FAIL.clear()
    install_fake_stores(tmp_path, monkeypatch)
    for name in ("WIZ_TOKEN", "GH_TOKEN", "UNTAPED_PROFILE"):
        monkeypatch.delenv(name, raising=False)


def _wiz() -> CapabilitySpec:
    return make_spec(
        "wiz",
        profile_model=WizProfile,
        doctor_checks=(
            connection_check("wiz.connection", section="wiz"),
            online_check("wiz.api", section="wiz", probe=_probe),
        ),
    )


def _cli(*args: str, specs: tuple[CapabilitySpec, ...] | None = None) -> CliResult:
    candidates = tuple(provider_candidate(spec) for spec in (specs or (_wiz(),)))
    root = bootstrap.build_root_app(candidates=candidates)
    return invoke_cli(root.meta, list(args))


def _plan(
    *args: str, profile: str | None = None, specs: tuple[CapabilitySpec, ...] | None = None
) -> list[dict[str, Any]]:
    root = ["--profile", profile] if profile else []
    result = _cli(*root, "setup", "plan", "--format", "json", *args, specs=specs)
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert isinstance(rows, list)
    return rows


def _step(rows: list[dict[str, Any]], step: str) -> dict[str, Any]:
    return next(row for row in rows if row["step"] == step)


def _configure(path: Path, wiz: str, profile: str = "default") -> None:
    write_config(path, f"profiles:\n  {profile}:\n    wiz: {wiz}\nactive: {profile}\n")


def test_an_empty_config_lists_every_step_with_its_command() -> None:
    rows = _plan()
    assert [row["step"] for row in rows] == ["profile", "wiz.base_url", "wiz.token", "wiz.online"]
    assert rows[0] == {
        "step": "profile",
        "capability": "untaped",
        "state": "done",
        "detail": "profile default exists",
        "run": [],
        "by": "agent",
    }
    assert _step(rows, "wiz.base_url") | {"detail": ""} == {
        "step": "wiz.base_url",
        "capability": "wiz",
        "state": "todo",
        "detail": "",
        "run": ["--profile", "default", "config", "set", "wiz.base_url", "<URL>"],
        "by": "agent",
    }
    token = _step(rows, "wiz.token")
    assert (token["state"], token["by"]) == ("todo", "user")
    assert token["run"] == [
        *("--profile", "default", "config", "set"),
        *("wiz.token_command", "<COMMAND>"),
    ]
    assert _step(rows, "wiz.online")["state"] == "skipped"


def test_with_a_password_store_the_token_step_is_auth_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    token = _step(_plan(), "wiz.token")
    assert token["run"] == ["--profile", "default", "auth", "set", "wiz"]
    assert (token["state"], token["by"]) == ("todo", "user")
    assert "pass" in token["detail"]


def test_a_plaintext_token_fails_and_moves_with_auth_migrate(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    _configure(_isolated_config, "{base_url: https://wiz, token: s3cret}")
    result = _cli("setup", "plan", "--format", "json")
    token = _step(json.loads(result.stdout), "wiz.token")
    assert (token["state"], token["by"]) == ("failed", "user")
    assert token["run"] == ["--profile", "default", "auth", "migrate"]
    for fmt in ("json", "yaml", "table", "pipe"):
        assert "s3cret" not in _cli("setup", "plan", "--format", fmt).output


def test_a_plaintext_token_without_a_store_goes_back_to_the_wizard(_isolated_config: Path) -> None:
    _configure(_isolated_config, "{base_url: https://wiz, token: s3cret}")
    token = _step(_plan(), "wiz.token")
    assert (token["state"], token["by"]) == ("failed", "user")
    assert token["run"] == ["--profile", "default", "setup", "--only", "wiz"]


@pytest.mark.parametrize(
    ("wiz", "env", "detail"),
    [
        ("{base_url: https://wiz, token_command: [x]}", {}, "token from wiz.token_command"),
        ("{base_url: https://wiz}", {"WIZ_TOKEN": "e"}, "token from $WIZ_TOKEN"),
    ],
)
def test_a_token_outside_the_config_is_done(
    _isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    wiz: str,
    env: dict[str, str],
    detail: str,
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    _configure(_isolated_config, wiz)
    rows = _plan()
    assert _step(rows, "wiz.base_url")["state"] == "done"
    token = _step(rows, "wiz.token")
    assert (token["state"], token["detail"], token["run"]) == ("done", detail, [])


def test_a_service_without_token_command_is_told_to_export_a_variable() -> None:
    legacy = make_spec("legacy", profile_model=LegacyProfile)
    token = _step(_plan(specs=(legacy,)), "legacy.token")
    assert (token["state"], token["by"], token["run"]) == ("todo", "user", [])
    assert "$UNTAPED_LEGACY__TOKEN" in token["detail"]


def test_no_step_ever_stores_a_token_in_the_config(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = make_spec("legacy", profile_model=LegacyProfile)
    for stores in ((), ("pass",)):
        install_fake_stores(tmp_path, monkeypatch, *stores)
        for rows in (_plan(specs=(_wiz(), legacy)), _plan(profile="work", specs=(_wiz(),))):
            for row in rows:
                assert not any(arg.endswith(".token") for arg in row["run"]), row


def test_a_missing_profile_is_created_first_and_every_step_targets_it() -> None:
    rows = _plan(profile="work")
    assert rows[0]["state"] == "todo"
    assert rows[0]["run"] == ["profile", "create", "work"]
    assert _step(rows, "wiz.base_url")["run"][:2] == ["--profile", "work"]
    assert _step(rows, "wiz.online")["state"] == "skipped"


def test_invalid_settings_fail_their_service_only(_isolated_config: Path) -> None:
    hub = make_spec("hub", profile_model=HubProfile)
    _configure(_isolated_config, "{base_url: https://wiz, token_command: []}")
    rows = _plan(specs=(_wiz(), hub))
    settings = _step(rows, "wiz.settings")
    assert settings["state"] == "failed"
    assert "wiz settings are invalid" in settings["detail"]
    assert "wiz.base_url" not in {row["step"] for row in rows}
    assert _step(rows, "hub.base_url")["state"] == "done"


def test_only_narrows_and_orders_the_services() -> None:
    hub = make_spec("hub", profile_model=HubProfile)
    rows = _plan("--only", "hub,wiz", specs=(_wiz(), hub))
    assert [row["capability"] for row in rows[1:]] == ["hub", "hub", "wiz", "wiz", "wiz"]
    assert {row["capability"] for row in _plan("--only", "hub", specs=(_wiz(), hub))} == {
        "untaped",
        "hub",
    }


def test_an_unknown_only_name_is_a_usage_error() -> None:
    result = _cli("setup", "plan", "--only", "nope")
    assert result.exit_code == 2
    assert "service not found: 'nope'; known: wiz" in result.stderr


def test_online_runs_the_services_own_online_check(_isolated_config: Path) -> None:
    _configure(_isolated_config, "{base_url: https://wiz, token_command: [x]}")
    online = _step(_plan("--online"), "wiz.online")
    assert (online["state"], online["detail"], online["run"]) == (
        "done",
        "authenticated as alice",
        [],
    )
    assert _PROBES == ["probed"]


def test_a_failed_online_check_carries_its_fix(_isolated_config: Path) -> None:
    _configure(_isolated_config, "{base_url: https://wiz, token_command: [x]}")
    _FAIL.append(True)
    online = _step(_plan("--online"), "wiz.online")
    assert (online["state"], online["by"]) == ("failed", "user")
    assert online["run"] == ["--profile", "default", "auth", "set", "wiz"]


def test_online_skips_services_that_are_not_ready() -> None:
    online = _step(_plan("--online"), "wiz.online")
    assert online["state"] == "skipped"
    assert _PROBES == []


def test_check_exits_3_while_a_step_is_pending(_isolated_config: Path) -> None:
    assert _cli("setup", "plan", "--check", "--format", "json").exit_code == 3
    _configure(_isolated_config, "{base_url: https://wiz, token_command: [x]}")
    assert _cli("setup", "plan", "--check", "--format", "json").exit_code == 0
    assert _cli("setup", "plan", "--check", "--online", "--format", "json").exit_code == 0
    _FAIL.append(True)
    assert _cli("setup", "plan", "--check", "--online", "--format", "json").exit_code == 3


def test_running_the_agent_steps_completes_them(_isolated_config: Path) -> None:
    values = {"<URL>": "https://wiz.example"}
    for row in _plan():
        if row["state"] == "todo" and row["by"] == "agent":
            argv = [values.get(arg, arg) for arg in row["run"]]
            assert _cli(*argv).exit_code == 0
    assert _step(_plan(), "wiz.base_url") | {"run": []} == {
        "step": "wiz.base_url",
        "capability": "wiz",
        "state": "done",
        "detail": "https://wiz.example",
        "run": [],
        "by": "agent",
    }


def test_plan_wizard_state_and_doctor_agree(_isolated_config: Path) -> None:
    for wiz, configured in (
        ("{}", False),
        ("{base_url: https://wiz}", True),
        ("{base_url: https://wiz, token_command: [x]}", True),
    ):
        _configure(_isolated_config, wiz)
        token_done = _step(_plan(), "wiz.token")["state"] == "done"
        doctor = _cli("doctor", "--format", "json")
        connection = next(
            row for row in json.loads(doctor.stdout) if row["check"] == "wiz.connection"
        )
        assert (connection["detail"] != "not configured") is configured
        assert token_done is (connection["status"] == "pass" and configured)


def test_the_table_shows_each_command_line() -> None:
    result = _cli("setup", "plan", "--format", "table")
    assert "untaped --profile default config set wiz.base_url '<URL>'" in result.stdout


def test_a_github_style_service_suggests_gh_without_running_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "gh-ran"
    gh = tmp_path / "fake-bin" / "gh"
    gh.write_text(f"#!{sys.executable}\nopen({str(marker)!r}, 'w').close()\n", encoding="utf-8")
    gh.chmod(0o755)
    hub = make_spec("hub", profile_model=HubProfile)
    token = _step(_plan(specs=(hub,)), "hub.token")
    assert "after `gh auth login`" in token["detail"]
    assert "config set hub.token_command" in token["detail"]
    assert not marker.exists()


def test_without_gh_there_is_no_suggestion() -> None:
    hub = make_spec("hub", profile_model=HubProfile)
    assert "gh auth" not in _step(_plan(specs=(hub,)), "hub.token")["detail"]


def test_without_a_service_there_is_nothing_to_plan() -> None:
    result = _cli("setup", "plan", specs=(make_spec("plain"),))
    assert result.exit_code == 4
    assert "no composed capability takes a base URL and token" in result.stderr
