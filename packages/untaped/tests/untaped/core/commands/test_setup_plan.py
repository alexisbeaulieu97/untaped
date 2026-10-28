"""``untaped setup plan``: a profile's remaining setup steps as rows an agent acts on.

Every ``run`` is a complete argv (``--profile`` first, ``<NAME>``
placeholders), every step that asks for or reveals a secret is ``by:
user``, and the online rows are the plugins' own online doctor checks.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel, HttpUrl

from core_test_support.commands.stores import install_fake_stores
from core_test_support.commands.support import (
    FAIL,
    PROBES,
    WIZ_TOKEN,
    WizProfile,
    WizServer,
    make_plugin,
    wiz_api,
    write_config,
)
from untaped.core.commands import setup_plan
from untaped.runtime import bootstrap
from untaped.sdk import (
    Credential,
    CredentialKey,
    Plugin,
    Suggest,
)
from untaped.sdk.health.doctor import DoctorCheck, DoctorResult, PluginContext
from untaped.sdk.testing import (
    CliResult,
    ScreenKeys,
    ScriptedPromptBackend,
    invoke_cli,
    plugin_candidate,
)

pytestmark = pytest.mark.usefixtures("_isolated_config", "home_run")


class HubServer(CredentialKey):
    """The API a ``hub`` token is for."""

    url: HttpUrl = HttpUrl("https://hub.example")


class HubProfile(BaseModel):
    """GitHub-like double: a default URL, ``GH_TOKEN`` and a ``gh`` suggestion (section ``hub``)."""

    server: HubServer = HubServer()


def _hub() -> type[Plugin]:
    """``hub``, whose token is read from ``$GH_TOKEN`` or suggested from ``gh auth token``."""
    token = Credential(
        key=HubServer,
        env=("GH_TOKEN",),
        suggest=Suggest(("gh", "auth", "token"), note="after `gh auth login`"),
    )
    return make_plugin("hub", settings=HubProfile, token=token)


@pytest.fixture(autouse=True)
def _clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No store, no ``gh`` and no ambient token unless a test adds one."""
    PROBES.clear()
    FAIL.clear()
    install_fake_stores(tmp_path, monkeypatch)
    for name in ("WIZ_TOKEN", "GH_TOKEN", "UNTAPED_PROFILE"):
        monkeypatch.delenv(name, raising=False)


def _wiz() -> type[Plugin]:
    return make_plugin("wiz", settings=WizProfile, api=wiz_api())


def _cli(*args: str, specs: tuple[type[Plugin], ...] | None = None) -> CliResult:
    candidates = tuple(plugin_candidate(spec) for spec in (specs or (_wiz(),)))
    root = bootstrap.build_root_app(candidates=candidates)
    return invoke_cli(root.meta, list(args))


def _plan(
    *args: str, profile: str | None = None, specs: tuple[type[Plugin], ...] | None = None
) -> list[dict[str, Any]]:
    root = ["--profile", profile] if profile else []
    result = _cli(*root, "setup", "plan", "--format", "json", *args, specs=specs)
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert isinstance(rows, list)
    return rows


def _step(rows: list[dict[str, Any]], step: str) -> dict[str, Any]:
    return next(row for row in rows if row["step"] == step)


def _configure(path: Path, wiz: str) -> None:
    """Write ``wiz``'s settings to the base; a ``token_command`` becomes a stored login."""
    data = yaml.safe_load(wiz)
    command = data.pop("token_command", None)
    write_config(path, f"format_version: 2\nwiz: {json.dumps(data)}\n")
    if command:
        WIZ_TOKEN.store(WizServer(url=HttpUrl(data["server"]["url"])), command=command)


def test_an_empty_config_lists_every_step_with_its_command() -> None:
    rows = _plan()
    assert [row["step"] for row in rows] == [
        "install",
        "profile",
        "wiz.server",
        "wiz.token",
        "wiz.online.api",
    ]
    assert _step(rows, "profile") == {
        "step": "profile",
        "plugin": "untaped",
        "state": "done",
        "detail": "profile default exists",
        "run": [],
        "by": "agent",
    }
    assert _step(rows, "wiz.server") | {"detail": ""} == {
        "step": "wiz.server",
        "plugin": "wiz",
        "state": "todo",
        "detail": "",
        "run": ["--profile", "default", "config", "set", "wiz.server.url", "<URL>"],
        "by": "agent",
    }
    token = _step(rows, "wiz.token")
    assert (token["state"], token["by"]) == ("todo", "user")
    assert token["run"] == ["--profile", "default", "auth", "login", "wiz"]
    assert _step(rows, "wiz.online.api")["state"] == "skipped"


def test_the_first_step_registers_this_install() -> None:
    install = _step(_plan(), "install")
    assert (install["state"], install["by"], install["plugin"]) == ("todo", "agent", "untaped")
    assert install["run"] == ["--profile", "default", "setup", "register"]
    assert "so cleanup keeps the store layouts it uses" in install["detail"]

    assert _cli("setup", "register").exit_code == 0
    install = _step(_plan(), "install")
    assert (install["state"], install["run"]) == ("done", [])
    assert install["detail"].startswith("registered: untaped ")


def test_a_source_tree_cannot_register(monkeypatch: pytest.MonkeyPatch) -> None:
    from untaped.sdk.storage import _refs as refs

    monkeypatch.setattr(refs, "environment", lambda: None)
    install = _step(_plan(), "install")
    assert (install["state"], install["run"]) == ("skipped", [])
    assert "source tree" in install["detail"]


def test_the_token_step_is_auth_login_with_or_without_a_password_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for stores in ((), ("pass",)):
        install_fake_stores(tmp_path, monkeypatch, *stores)
        token = _step(_plan(), "wiz.token")
        assert token["run"] == ["--profile", "default", "auth", "login", "wiz"]
        assert (token["state"], token["by"]) == ("todo", "user")
        assert token["detail"] == "no wiz token for this run's wiz settings"


@pytest.mark.parametrize(
    ("wiz", "env", "detail"),
    [
        ("{server: {url: https://wiz}, token_command: [x]}", {}, "token from stored "),
        ("{server: {url: https://wiz}}", {"WIZ_TOKEN": "e"}, "token from $WIZ_TOKEN"),
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
    assert _step(rows, "wiz.server") | {"run": []} == {
        "step": "wiz.server",
        "plugin": "wiz",
        "state": "done",
        "detail": "url=https://wiz",
        "run": [],
        "by": "agent",
    }
    token = _step(rows, "wiz.token")
    assert (token["state"], token["run"]) == ("done", [])
    assert token["detail"].startswith(detail)


def test_no_step_ever_stores_a_token_in_the_config(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for stores in ((), ("pass",)):
        install_fake_stores(tmp_path, monkeypatch, *stores)
        for rows in (_plan(), _plan(profile="work")):
            for row in rows:
                assert not any(arg.endswith(".token") for arg in row["run"]), row


def test_a_missing_profile_is_created_first_and_every_step_targets_it() -> None:
    rows = _plan(profile="work")
    profile = _step(rows, "profile")
    assert profile["state"] == "todo"
    assert profile["run"] == ["profile", "create", "work"]
    assert _step(rows, "wiz.server")["run"][:2] == ["--profile", "work"]
    assert _step(rows, "wiz.online.api")["state"] == "skipped"


def test_invalid_settings_fail_their_service_only(_isolated_config: Path) -> None:
    hub = _hub()
    _configure(_isolated_config, "{server: {url: not a url}}")
    rows = _plan(specs=(_wiz(), hub))
    settings = _step(rows, "wiz.settings")
    assert settings["state"] == "failed"
    assert "wiz settings are invalid" in settings["detail"]
    assert "wiz.server" not in {row["step"] for row in rows}
    assert _step(rows, "hub.token")["state"] == "todo"


def test_only_narrows_and_orders_the_services() -> None:
    hub = _hub()
    rows = _plan("--only", "hub,wiz", specs=(_wiz(), hub))
    # hub marks only its token (its URL has a default), so it has one row
    assert [row["plugin"] for row in rows[2:]] == ["hub", "wiz", "wiz", "wiz"]
    assert {row["plugin"] for row in _plan("--only", "hub", specs=(_wiz(), hub))} == {
        "untaped",
        "hub",
    }


def test_an_unknown_only_name_is_a_usage_error() -> None:
    result = _cli("setup", "plan", "--only", "nope")
    assert result.exit_code == 2
    assert "section not found: 'nope'" in result.stderr


def test_online_runs_the_services_own_online_check(_isolated_config: Path) -> None:
    _configure(_isolated_config, "{server: {url: https://wiz}, token_command: [x]}")
    online = _step(_plan("--online"), "wiz.online.api")
    assert (online["state"], online["detail"], online["run"]) == (
        "done",
        "authenticated as alice",
        [],
    )
    assert PROBES == ["probed"]


def test_a_failed_online_check_carries_its_fix(_isolated_config: Path) -> None:
    _configure(_isolated_config, "{server: {url: https://wiz}, token_command: [x]}")
    FAIL.append(True)
    online = _step(_plan("--online"), "wiz.online.api")
    assert (online["state"], online["by"]) == ("failed", "user")
    assert online["run"] == ["--profile", "default", "auth", "login", "wiz"]


def test_online_skips_services_that_are_not_ready() -> None:
    online = _step(_plan("--online"), "wiz.online.api")
    assert online["state"] == "skipped"
    assert PROBES == []


def test_check_exits_3_while_a_step_is_pending(_isolated_config: Path) -> None:
    assert _cli("setup", "plan", "--check", "--format", "json").exit_code == 3
    _configure(_isolated_config, "{server: {url: https://wiz}, token_command: [x]}")
    # an unregistered install is advice, not a pending step: --check answers as before
    assert _step(_plan(), "install")["state"] == "todo"
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
    assert _step(_plan(), "wiz.server") | {"run": []} == {
        "step": "wiz.server",
        "plugin": "wiz",
        "state": "done",
        "detail": "url=https://wiz.example",
        "run": [],
        "by": "agent",
    }


@pytest.mark.parametrize(
    ("wiz", "env", "configured", "rejected"),
    [
        ("{}", {}, False, False),
        ("{server: {url: https://wiz}}", {}, True, False),
        ("{server: {url: https://wiz}, token_command: [x]}", {}, True, False),
        ("{server: {url: https://wiz}, token_command: [x]}", {}, True, True),
        (
            "{}",
            {"UNTAPED_WIZ__SERVER__URL": "https://wiz", "UNTAPED_WIZ__TOKEN_COMMAND": '["x"]'},
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
    setup, api = doctor["setup"], doctor["wiz.api"]
    assert (setup["detail"] != "not set up") is configured
    token_done = _step(rows, "wiz.token")["state"] == "done"
    assert token_done is (setup["status"] == "pass" and configured)
    online = _step(rows, "wiz.online.api")
    if token_done:
        assert (online["state"] == "failed") is (api["status"] == "fail")
        assert online["run"] == (api["fix"] or [])
    backend = ScriptedPromptBackend(screens=[ScreenKeys("esc")])
    root = bootstrap.build_root_app(candidates=(plugin_candidate(_wiz()),))
    setup = invoke_cli(
        root.meta, ["setup"], interactive=True, prompt_backend=backend, terminal=True
    )
    assert setup.exit_code == 0, setup.output
    # The screen lists the service as configured exactly when the plan and doctor do.
    row = next(row for row in backend.ran[0].init()[0].rows if row.name == "wiz")
    assert (row.status != "not configured") is configured


def test_an_env_configured_service_completes_the_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_WIZ__SERVER__URL", "https://wiz")
    monkeypatch.setenv("WIZ_TOKEN", "t")
    rows = _plan()
    assert _step(rows, "wiz.server")["state"] == "done"
    assert _step(rows, "wiz.token")["state"] == "done"
    assert _cli("setup", "register").exit_code == 0
    assert _cli("setup", "plan", "--online", "--check", "--format", "json").exit_code == 0


@pytest.mark.parametrize(
    ("run", "by"),
    [
        (["--profile", "auth", "config", "set", "wiz.server.url", "<URL>"], "agent"),
        (["--profile", "work", "auth", "login", "wiz"], "user"),
        (["auth", "login", "keyed", "api_key"], "user"),
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
            id="wiz.api", ok=False, detail="stale", fix="auth login wiz", automatic=automatic
        )

    check = DoctorCheck(title="wiz API reachable", run=run, online=True)
    wiz = make_plugin("wiz", settings=WizProfile, api=check)
    _configure(_isolated_config, "{server: {url: https://wiz}, token_command: [x]}")
    online = _step(_plan("--online", specs=(wiz,)), "wiz.online.api")
    assert (online["state"], online["by"]) == ("failed", by)
    assert online["run"] == ["--profile", "default", "auth", "login", "wiz"]


@pytest.mark.parametrize("spelling", [["--profile", "default"], ["--profile=default"]])
def test_a_fix_that_sets_a_token_is_the_users_however_it_names_the_profile(
    _isolated_config: Path, spelling: list[str]
) -> None:
    def run(_ctx: PluginContext) -> DoctorResult:
        return DoctorResult(
            id="wiz.api", ok=False, detail="no", fix=[*spelling, "auth", "login", "wiz"]
        )

    check = DoctorCheck(title="wiz API reachable", run=run, online=True)
    wiz = make_plugin("wiz", settings=WizProfile, api=check)
    _configure(_isolated_config, "{server: {url: https://wiz}, token_command: [x]}")
    online = _step(_plan("--online", specs=(wiz,)), "wiz.online.api")
    assert (online["state"], online["by"]) == ("failed", "user")


_VALUES = {
    "<URL>": "https://wiz.example",
    "<BASE_URL>": "https://wiz.example",
    "<COMMAND>": '["x"]',
    "<PATH>": "/ca.pem",
}


def test_every_run_parses(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    specs = (_wiz(),)
    root = bootstrap.build_root_app(candidates=tuple(plugin_candidate(s) for s in specs))
    runs: list[list[str]] = []
    for stores in ((), ("pass",)):
        install_fake_stores(tmp_path, monkeypatch, *stores)
        for config in (
            "format_version: 2\n",
            "format_version: 2\nwiz: {server: {url: https://w}}\n",
        ):
            write_config(_isolated_config, config)
            runs += [row["run"] for row in _plan(specs=specs) if row["run"]]
            runs += [row["run"] for row in _plan(profile="work", specs=specs) if row["run"]]
    FAIL.append(True)
    write_config(_isolated_config, "format_version: 2\nwiz: {server: {url: https://w}}\n")
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
    assert "untaped config set wiz.server.url '<URL>'" in result.stdout
    assert "--profile" not in result.stdout


def test_the_table_works_before_the_profile_exists() -> None:
    result = _cli("--profile", "work", "setup", "plan", "--check")
    assert result.exit_code == 3, result.output
    assert "untaped profile create work" in result.stdout
    assert "untaped --profile work config set wiz.server.url '<URL>'" in result.stdout


def test_a_github_style_service_suggests_gh_without_running_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "gh-ran"
    gh = tmp_path / "fake-bin" / "gh"
    gh.write_text(f"#!{sys.executable}\nopen({str(marker)!r}, 'w').close()\n", encoding="utf-8")
    gh.chmod(0o755)
    hub = _hub()
    token = _step(_plan(specs=(hub,)), "hub.token")
    assert "after `gh auth login`, let `gh auth token` print it" in token["detail"]
    assert (
        "untaped --profile default auth login hub --token-command 'gh auth token'"
        in (token["detail"])
    )
    assert not marker.exists()


def test_without_gh_there_is_no_suggestion() -> None:
    hub = _hub()
    assert "gh auth" not in _step(_plan(specs=(hub,)), "hub.token")["detail"]


def test_without_a_service_there_is_nothing_to_plan() -> None:
    result = _cli("setup", "plan", specs=(make_plugin("plain"),))
    assert result.exit_code == 4
    assert "no composed plugin marks settings to set up" in result.stderr


def test_only_naming_no_service_has_nothing_to_plan() -> None:
    result = _cli("setup", "plan", "--only", "untaped")
    assert result.exit_code == 4
    assert "none of the named sections is a plugin with settings to set up" in result.stderr


def test_the_table_draws_each_state_in_its_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
    result = _cli("setup", "plan")
    assert result.exit_code == 0, result.output
    assert "\x1b[33mskipped" in result.stdout  # wiz.online.api, a warning
