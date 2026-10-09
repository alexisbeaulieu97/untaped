"""``untaped setup plan``: a profile's remaining setup steps as rows an agent acts on.

Every ``run`` is a complete argv (``--profile`` first, ``<NAME>``
placeholders), every step that asks for or reveals a secret is ``by:
user``, and the online rows are the plugins' own online doctor checks.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, SecretStr

from test_management.stores import install_fake_stores
from test_management.support import (
    FAIL,
    PROBES,
    LegacyProfile,
    WizProfile,
    make_spec,
    wiz_api_check,
    wiz_probe,
    write_config,
)
from untaped import bootstrap
from untaped.management import setup_plan
from untaped.plugins.registry import (
    DoctorCheck,
    DoctorResult,
    PluginContext,
    PluginSpec,
)
from untaped.sdk import TokenCommand, TokenSources, connection_check, online_check
from untaped.testing import (
    CliResult,
    ScreenKeys,
    ScriptedPromptBackend,
    invoke_cli,
    provider_candidate,
)

pytestmark = pytest.mark.usefixtures("_isolated_config")


class HubProfile(BaseModel):
    """GitHub-like double: a default URL and ``GH_TOKEN`` (section ``hub``)."""

    token_sources: ClassVar[TokenSources] = TokenSources(env=("GH_TOKEN",))

    base_url: str = "https://hub.example"
    token: SecretStr | None = None
    token_command: TokenCommand = None


@pytest.fixture(autouse=True)
def _clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No store, no ``gh`` and no ambient token unless a test adds one."""
    PROBES.clear()
    FAIL.clear()
    install_fake_stores(tmp_path, monkeypatch)
    for name in ("WIZ_TOKEN", "GH_TOKEN", "UNTAPED_PROFILE"):
        monkeypatch.delenv(name, raising=False)


def _wiz() -> PluginSpec:
    return make_spec(
        "wiz",
        settings=WizProfile,
        doctor_checks=(
            connection_check("wiz.connection", section="wiz"),
            wiz_api_check(),
        ),
    )


def _cli(*args: str, specs: tuple[PluginSpec, ...] | None = None) -> CliResult:
    candidates = tuple(provider_candidate(spec) for spec in (specs or (_wiz(),)))
    root = bootstrap.build_root_app(candidates=candidates)
    return invoke_cli(root.meta, list(args))


def _plan(
    *args: str, profile: str | None = None, specs: tuple[PluginSpec, ...] | None = None
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
    assert [row["step"] for row in rows] == [
        "profile",
        "wiz.base_url",
        "wiz.token",
        "wiz.online.api",
    ]
    assert rows[0] == {
        "step": "profile",
        "plugin": "untaped",
        "state": "done",
        "detail": "profile default exists",
        "run": [],
        "by": "agent",
    }
    assert _step(rows, "wiz.base_url") | {"detail": ""} == {
        "step": "wiz.base_url",
        "plugin": "wiz",
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
    assert _step(rows, "wiz.online.api")["state"] == "skipped"


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
    # `auth migrate` moves the token inside its own process: no secret reaches the agent.
    assert (token["state"], token["by"]) == ("failed", "agent")
    assert token["run"] == ["--profile", "default", "auth", "migrate"]
    for fmt in ("json", "yaml", "table", "pipe"):
        assert "s3cret" not in _cli("setup", "plan", "--format", fmt).output


def test_a_plaintext_token_without_a_store_goes_back_to_setup(_isolated_config: Path) -> None:
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
    legacy = make_spec("legacy", settings=LegacyProfile)
    token = _step(_plan(specs=(legacy,)), "legacy.token")
    assert (token["state"], token["by"], token["run"]) == ("todo", "user", [])
    assert "$UNTAPED_LEGACY__TOKEN" in token["detail"]


def test_no_step_ever_stores_a_token_in_the_config(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = make_spec("legacy", settings=LegacyProfile)
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
    assert _step(rows, "wiz.online.api")["state"] == "skipped"


def test_invalid_settings_fail_their_service_only(_isolated_config: Path) -> None:
    hub = make_spec("hub", settings=HubProfile)
    _configure(_isolated_config, "{base_url: https://wiz, token_command: []}")
    rows = _plan(specs=(_wiz(), hub))
    settings = _step(rows, "wiz.settings")
    assert settings["state"] == "failed"
    assert "wiz settings are invalid" in settings["detail"]
    assert "wiz.base_url" not in {row["step"] for row in rows}
    assert _step(rows, "hub.base_url")["state"] == "done"


def test_only_narrows_and_orders_the_services() -> None:
    hub = make_spec("hub", settings=HubProfile)
    rows = _plan("--only", "hub,wiz", specs=(_wiz(), hub))
    assert [row["plugin"] for row in rows[1:]] == ["hub", "hub", "wiz", "wiz", "wiz"]
    assert {row["plugin"] for row in _plan("--only", "hub", specs=(_wiz(), hub))} == {
        "untaped",
        "hub",
    }


def test_an_unknown_only_name_is_a_usage_error() -> None:
    result = _cli("setup", "plan", "--only", "nope")
    assert result.exit_code == 2
    assert "service not found: 'nope'; known: wiz" in result.stderr


def test_online_runs_the_services_own_online_check(_isolated_config: Path) -> None:
    _configure(_isolated_config, "{base_url: https://wiz, token_command: [x]}")
    online = _step(_plan("--online"), "wiz.online.api")
    assert (online["state"], online["detail"], online["run"]) == (
        "done",
        "authenticated as alice",
        [],
    )
    assert PROBES == ["probed"]


def test_a_failed_online_check_carries_its_fix(_isolated_config: Path) -> None:
    _configure(_isolated_config, "{base_url: https://wiz, token_command: [x]}")
    FAIL.append(True)
    online = _step(_plan("--online"), "wiz.online.api")
    assert (online["state"], online["by"]) == ("failed", "user")
    assert online["run"] == ["--profile", "default", "auth", "set", "wiz"]


def test_online_skips_services_that_are_not_ready() -> None:
    online = _step(_plan("--online"), "wiz.online.api")
    assert online["state"] == "skipped"
    assert PROBES == []


def test_check_exits_3_while_a_step_is_pending(_isolated_config: Path) -> None:
    assert _cli("setup", "plan", "--check", "--format", "json").exit_code == 3
    _configure(_isolated_config, "{base_url: https://wiz, token_command: [x]}")
    assert _cli("setup", "plan", "--check", "--format", "json").exit_code == 0
    assert _cli("setup", "plan", "--check", "--online", "--format", "json").exit_code == 0
    FAIL.append(True)
    assert _cli("setup", "plan", "--check", "--online", "--format", "json").exit_code == 3


def test_running_the_agent_steps_completes_them(_isolated_config: Path) -> None:
    values = {"<URL>": "https://wiz.example"}
    for row in _plan():
        if row["state"] == "todo" and row["by"] == "agent":
            argv = [values.get(arg, arg) for arg in row["run"]]
            assert _cli(*argv).exit_code == 0
    assert _step(_plan(), "wiz.base_url") | {"run": []} == {
        "step": "wiz.base_url",
        "plugin": "wiz",
        "state": "done",
        "detail": "https://wiz.example",
        "run": [],
        "by": "agent",
    }


@pytest.mark.parametrize(
    ("wiz", "env", "configured", "rejected"),
    [
        ("{}", {}, False, False),
        ("{base_url: https://wiz}", {}, True, False),
        ("{base_url: https://wiz, token_command: [x]}", {}, True, False),
        ("{base_url: https://wiz, token_command: [x]}", {}, True, True),
        (
            "{}",
            {"UNTAPED_WIZ__BASE_URL": "https://wiz", "UNTAPED_WIZ__TOKEN_COMMAND": '["x"]'},
            True,
            False,
        ),
    ],
)
def test_plan_setup_screen_and_doctor_agree(
    _isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    wiz: str,
    env: dict[str, str],
    configured: bool,
    rejected: bool,
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    if rejected:
        FAIL.append(True)
    _configure(_isolated_config, wiz)
    rows = _plan("--online")
    doctor = {
        row["check"]: row
        for row in json.loads(_cli("doctor", "--online", "--format", "json").stdout)
    }
    connection, api = doctor["wiz.connection"], doctor["wiz.api"]
    assert (connection["detail"] != "not configured") is configured
    token_done = _step(rows, "wiz.token")["state"] == "done"
    assert token_done is (connection["status"] == "pass" and configured)
    online = _step(rows, "wiz.online.api")
    if token_done:
        assert (online["state"] == "failed") is (api["status"] == "fail")
        assert online["run"] == (api["fix"] or [])
    backend = ScriptedPromptBackend(screens=[ScreenKeys("esc")])
    root = bootstrap.build_root_app(candidates=(provider_candidate(_wiz()),))
    setup = invoke_cli(
        root.meta, ["setup"], interactive=True, prompt_backend=backend, terminal=True
    )
    assert setup.exit_code == 0, setup.output
    # The screen lists the service as configured exactly when the plan and doctor do.
    (row,) = backend.ran[0].init()[0].rows
    assert (row.status != "not configured") is configured


def test_an_env_configured_service_completes_the_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_WIZ__BASE_URL", "https://wiz")
    monkeypatch.setenv("UNTAPED_WIZ__TOKEN_COMMAND", '["x"]')
    rows = _plan()
    assert _step(rows, "wiz.base_url")["state"] == "done"
    assert _step(rows, "wiz.token")["state"] == "done"
    assert _cli("setup", "plan", "--online", "--check", "--format", "json").exit_code == 0


def test_a_plaintext_token_in_default_fails_the_profile_that_inherits_it(
    _isolated_config: Path,
) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    wiz: {token: s3cret}\n"
        "  work:\n    wiz: {base_url: https://wiz, token_command: [x]}\n",
    )
    token = _step(_plan(profile="work"), "wiz.token")
    assert (token["state"], token["by"]) == ("failed", "user")
    assert "in profile default" in token["detail"]
    assert token["run"] == ["--profile", "default", "setup", "--only", "wiz"]


def test_a_plaintext_token_without_token_command_is_exported_instead(
    _isolated_config: Path,
) -> None:
    legacy = make_spec("legacy", settings=LegacyProfile)
    write_config(
        _isolated_config, "profiles:\n  default:\n    legacy: {base_url: https://l, token: t}\n"
    )
    token = _step(_plan(specs=(legacy,)), "legacy.token")
    assert (token["state"], token["by"]) == ("failed", "user")
    assert "export $UNTAPED_LEGACY__TOKEN" in token["detail"]
    assert token["run"] == ["--profile", "default", "config", "unset", "legacy.token"]


def test_a_rejected_token_without_token_command_is_never_stored_in_the_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = make_spec(
        "legacy",
        settings=LegacyProfile,
        doctor_checks=(online_check("legacy.api", section="legacy", probe=wiz_probe),),
    )
    monkeypatch.setenv("UNTAPED_LEGACY__BASE_URL", "https://l")
    monkeypatch.setenv("UNTAPED_LEGACY__TOKEN", "t")
    FAIL.append(True)
    online = _step(_plan("--online", specs=(legacy,)), "legacy.online.api")
    assert (online["state"], online["by"], online["run"]) == ("failed", "user", [])
    assert "export $UNTAPED_LEGACY__TOKEN with a working token" in online["detail"]


@pytest.mark.parametrize(
    ("run", "by"),
    [
        (["--profile", "auth", "config", "set", "wiz.base_url", "<URL>"], "agent"),
        (["--profile", "work", "auth", "set", "wiz"], "user"),
        (["--profile", "work", "config", "set", "wiz.token_command", "<COMMAND>"], "user"),
    ],
)
def test_who_runs_a_manual_fix_depends_on_its_command(run: list[str], by: str) -> None:
    assert setup_plan._by(run, automatic=False) == by


@pytest.mark.parametrize(("automatic", "by"), [(True, "agent"), (False, "user")])
def test_an_online_rows_runner_follows_the_doctor_rows_automatic(
    _isolated_config: Path, automatic: bool, by: str
) -> None:
    def run(_ctx: PluginContext) -> DoctorResult:
        return DoctorResult(
            id="wiz.api", ok=False, detail="stale", fix="auth migrate", automatic=automatic
        )

    check = DoctorCheck(id="wiz.api", title="wiz API reachable", run=run, online=True)
    wiz = make_spec("wiz", settings=WizProfile, doctor_checks=(check,))
    _configure(_isolated_config, "{base_url: https://wiz, token_command: [x]}")
    online = _step(_plan("--online", specs=(wiz,)), "wiz.online.api")
    assert (online["state"], online["by"]) == ("failed", by)
    assert online["run"] == ["--profile", "default", "auth", "migrate"]


@pytest.mark.parametrize("spelling", [["--profile", "default"], ["--profile=default"]])
def test_a_fix_that_sets_a_token_is_the_users_however_it_names_the_profile(
    _isolated_config: Path, spelling: list[str]
) -> None:
    def run(_ctx: PluginContext) -> DoctorResult:
        return DoctorResult(
            id="wiz.api", ok=False, detail="no", fix=[*spelling, "auth", "set", "wiz"]
        )

    check = DoctorCheck(id="wiz.api", title="wiz API reachable", run=run, online=True)
    wiz = make_spec("wiz", settings=WizProfile, doctor_checks=(check,))
    _configure(_isolated_config, "{base_url: https://wiz, token_command: [x]}")
    online = _step(_plan("--online", specs=(wiz,)), "wiz.online.api")
    assert (online["state"], online["by"]) == ("failed", "user")


_VALUES = {"<URL>": "https://wiz.example", "<COMMAND>": '["x"]', "<PATH>": "/ca.pem"}


def test_every_run_parses(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = make_spec("legacy", settings=LegacyProfile)
    specs = (_wiz(), legacy)
    root = bootstrap.build_root_app(candidates=tuple(provider_candidate(s) for s in specs))
    runs: list[list[str]] = []
    for stores in ((), ("pass",)):
        install_fake_stores(tmp_path, monkeypatch, *stores)
        for config in (
            "profiles: {}\n",
            "profiles:\n  default:\n    wiz: {base_url: https://w, token: t}\n"
            "    legacy: {base_url: https://l, token: t}\n",
        ):
            write_config(_isolated_config, config)
            runs += [row["run"] for row in _plan(specs=specs) if row["run"]]
            runs += [row["run"] for row in _plan(profile="work", specs=specs) if row["run"]]
    FAIL.append(True)
    write_config(_isolated_config, "profiles:\n  default:\n    wiz: {base_url: https://w}\n")
    monkeypatch.setenv("WIZ_TOKEN", "t")
    runs += [row["run"] for row in _plan("--online", specs=specs) if row["run"]]
    commands = {tuple(run[2:] if run[0] == "--profile" else run) for run in runs}
    assert {command[0] for command in commands} >= {"auth", "config", "profile", "setup"}
    for command in commands:
        root.parse_args(
            [_VALUES.get(arg, arg) for arg in command],
            exit_on_error=False,
            print_error=False,
            help_on_error=False,
        )


def test_the_table_shows_each_command_line() -> None:
    result = _cli("setup", "plan", "--format", "table")
    assert result.exit_code == 0, result.output
    # No `--profile` flag chose the profile, so the line goes without it.
    assert "untaped config set wiz.base_url '<URL>'" in result.stdout
    assert "--profile" not in result.stdout


def test_the_table_works_before_the_profile_exists() -> None:
    result = _cli("--profile", "work", "setup", "plan", "--check")
    assert result.exit_code == 3, result.output
    assert "untaped profile create work" in result.stdout
    assert "untaped --profile work config set wiz.base_url '<URL>'" in result.stdout


def test_a_github_style_service_suggests_gh_without_running_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "gh-ran"
    gh = tmp_path / "fake-bin" / "gh"
    gh.write_text(f"#!{sys.executable}\nopen({str(marker)!r}, 'w').close()\n", encoding="utf-8")
    gh.chmod(0o755)
    hub = make_spec("hub", settings=HubProfile)
    token = _step(_plan(specs=(hub,)), "hub.token")
    assert "after `gh auth login`" in token["detail"]
    assert "config set hub.token_command" in token["detail"]
    assert not marker.exists()


def test_without_gh_there_is_no_suggestion() -> None:
    hub = make_spec("hub", settings=HubProfile)
    assert "gh auth" not in _step(_plan(specs=(hub,)), "hub.token")["detail"]


def test_without_a_service_there_is_nothing_to_plan() -> None:
    result = _cli("setup", "plan", specs=(make_spec("plain"),))
    assert result.exit_code == 4
    assert "no composed plugin takes a base URL and token" in result.stderr
