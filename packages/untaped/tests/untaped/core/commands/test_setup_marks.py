"""Core's ``setup`` doctor row and the set rules, read from a plugin's setting marks.

One reading of "set" serves doctor, ``setup plan`` and the online checks: a
value from the active profile, the ``default`` profile it inherits, an
``UNTAPED_*`` variable (a leaf or a JSON object) counts; a blank string does
not, ``[]`` does. A secret's conventional variable is ambient: it counts
only once every other marked field is set. A stored command counts without
running.
"""

from __future__ import annotations

import json
import ssl
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import pytest
from pydantic import BaseModel, HttpUrl, SecretStr

from core_test_support.commands.stores import install_fake_stores
from core_test_support.commands.support import (
    FAIL,
    PROBES,
    WIZ_TOKEN,
    ExtProfile,
    WizProfile,
    WizServer,
    make_spec,
    wiz_api_check,
    write_config,
)
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginSpec
from untaped.sdk import (
    Credential,
    CredentialKey,
    HttpError,
    HttpTransportError,
    NeedsSetup,
    online_check,
)
from untaped.sdk.marks import forget_notices
from untaped.sdk.testing import (
    CliResult,
    invoke_cli,
    plugin_candidate,
)

pytestmark = pytest.mark.usefixtures("_isolated_config", "home_run")


class CrewProfile(BaseModel):
    """A plugin whose one marked field is a list (section ``crew``)."""

    teams: Annotated[list[str] | None, NeedsSetup] = None


class SoloKey(CredentialKey):
    """A fixed endpoint: the key has no fields."""

    origin = HttpUrl("https://solo.example.com")


class SoloProfile(BaseModel):
    """A plugin whose one need is its credential (section ``solo``)."""


SOLO_TOKEN = Credential("solo", "token", key=SoloKey, env=("SOLO_TOKEN",))


@pytest.fixture(autouse=True)
def _clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No store, no ambient token, a fresh once-per-run warning memory."""
    PROBES.clear()
    FAIL.clear()
    forget_notices()
    install_fake_stores(tmp_path, monkeypatch)
    for name in ("WIZ_TOKEN", "UNTAPED_PROFILE", "UNTAPED_WIZ", "UNTAPED_WIZ__BASE_URL"):
        monkeypatch.delenv(name, raising=False)


#: ``wiz``'s server, as a config line under a profile.
_SERVER = "wiz: {server: {url: https://wiz}}"


def _login(token: str = "t") -> None:
    """Store a ``wiz`` token for ``https://wiz``, as ``auth login wiz`` would."""
    WIZ_TOKEN.store(WizServer(url=HttpUrl("https://wiz")), token=token)


def _wiz() -> PluginSpec:
    return make_spec("wiz", settings=WizProfile, doctor_checks=(wiz_api_check(),))


def _cli(*args: str, specs: tuple[PluginSpec, ...] | None = None) -> CliResult:
    candidates = tuple(plugin_candidate(spec) for spec in (specs or (_wiz(),)))
    root = bootstrap.build_root_app(candidates=candidates)
    return invoke_cli(root.meta, list(args))


def _doctor(
    *args: str, specs: tuple[PluginSpec, ...] | None = None, exit_code: int = 0
) -> dict[str, dict[str, Any]]:
    result = _cli("doctor", "--format", "json", *args, specs=specs)
    assert result.exit_code == exit_code, result.output
    return {f"{row['check']}:{row['plugin']}": row for row in json.loads(result.stdout)}


def _setup(**kwargs: Any) -> dict[str, Any]:
    plugin = kwargs.pop("plugin", "wiz")
    return _doctor(**kwargs)[f"setup:{plugin}"]


def test_an_unused_plugin_passes_as_not_set_up() -> None:
    row = _setup()
    assert (row["status"], row["title"], row["detail"]) == ("pass", "setup", "not set up")
    assert row["fix"] is None


def test_half_set_warns_naming_what_is_missing_with_its_fix(_isolated_config: Path) -> None:
    write_config(_isolated_config, f"format_version: 2\n{_SERVER}\n")
    row = _setup()
    assert row["status"] == "warn"
    assert row["detail"] == "1 of 2 set; missing wiz.token"
    assert row["fix"][-3:] == ["auth", "login", "wiz"]


def test_a_missing_key_names_config_set_with_its_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_WIZ__TOKEN", "t")
    row = _setup()
    assert row["detail"] == "1 of 2 set; missing wiz.server"
    assert row["fix"][-4:] == ["config", "set", "wiz.server.url", "<URL>"]


@pytest.mark.parametrize(
    ("config", "env", "profile"),
    [
        pytest.param(f"{_SERVER}\n", {}, None, id="profile"),
        pytest.param(f"{_SERVER}\nprofiles:\n  work: {{}}\n", {}, "work", id="default-profile"),
        pytest.param("", {"UNTAPED_WIZ__SERVER__URL": "https://wiz"}, None, id="leaf-variable"),
        pytest.param(
            "",
            {"UNTAPED_WIZ": '{"server": {"url": "https://wiz"}}'},
            None,
            id="json-object-variable",
        ),
    ],
)
def test_a_value_counts_wherever_settings_resolve_it(
    _isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: str,
    env: dict[str, str],
    profile: str | None,
) -> None:
    write_config(_isolated_config, f"format_version: 2\n{config}")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    _login()
    args = ("--profile", profile) if profile else ()
    result = _cli(*args, "doctor", "--format", "json")
    row = next(r for r in json.loads(result.stdout) if r["check"] == "setup")
    assert row["status"] == "pass"
    assert row["detail"].startswith("url=https://wiz; token from stored ")


class NamedProfile(BaseModel):
    """A plugin whose one marked field is a string (section ``named``)."""

    name: Annotated[str | None, NeedsSetup] = None


def test_a_blank_string_is_not_set(_isolated_config: Path) -> None:
    named = make_spec("named", settings=NamedProfile)
    write_config(_isolated_config, "format_version: 2\nnamed: {name: '  '}\n")
    assert _setup(specs=(named,), plugin="named")["detail"] == "not set up"


def test_an_empty_list_is_set(_isolated_config: Path) -> None:
    crew = make_spec("crew", settings=CrewProfile)
    write_config(_isolated_config, "format_version: 2\ncrew: {teams: []}\n")
    row = _setup(specs=(crew,), plugin="crew")
    assert (row["status"], row["detail"]) == ("pass", "[]")


def test_an_ambient_variable_alone_does_not_set_up_a_plugin_with_other_marks(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WIZ_TOKEN", "ambient")
    assert _setup()["detail"] == "not set up"
    write_config(_isolated_config, f"format_version: 2\n{_SERVER}\n")
    row = _setup()
    assert (row["status"], row["detail"]) == ("pass", "url=https://wiz; token from $WIZ_TOKEN")


def test_a_token_command_counts_without_running(_isolated_config: Path, tmp_path: Path) -> None:
    ran = tmp_path / "ran"
    write_config(_isolated_config, f"format_version: 2\n{_SERVER}\n")
    WIZ_TOKEN.store(
        WizServer(url=HttpUrl("https://wiz")), command=["sh", "-c", f"touch {ran}; exit 1"]
    )
    assert _setup()["status"] == "pass"
    assert not ran.exists()


def test_every_marked_field_set_runs_the_online_check(_isolated_config: Path) -> None:
    write_config(_isolated_config, f"format_version: 2\n{_SERVER}\n")
    _login()
    rows = _doctor("--online")
    assert rows["wiz.api:wiz"]["status"] == "pass"
    assert PROBES == ["probed"]


def test_the_online_check_waits_for_every_marked_field(_isolated_config: Path) -> None:
    write_config(_isolated_config, f"format_version: 2\n{_SERVER}\n")
    rows = _doctor("--online")
    assert rows["wiz.api:wiz"]["detail"] == "skipped: not set up"
    assert PROBES == []


def test_a_rejected_token_names_auth_login(_isolated_config: Path) -> None:
    FAIL.append(True)
    write_config(_isolated_config, f"format_version: 2\n{_SERVER}\n")
    _login()
    row = _doctor("--online", exit_code=1)["wiz.api:wiz"]
    assert row["status"] == "fail"
    assert row["fix"][-3:] == ["auth", "login", "wiz"]
    assert "the token can also come from $WIZ_TOKEN" in row["detail"]


def test_a_plugin_without_a_service_never_warns() -> None:
    result = _cli("doctor", "--format", "json", specs=(make_spec("ext", settings=ExtProfile),))
    assert "NeedsSetup" not in result.stderr
    assert all(row["check"] != "setup" for row in json.loads(result.stdout))


def _raising(exc: Exception) -> Callable[[], str]:
    def probe() -> str:
        raise exc

    return probe


def _untrusted() -> Exception:
    cause = ssl.SSLCertVerificationError(1, "certificate verify failed")
    cause.verify_code = 20
    try:
        raise HttpTransportError("[SSL] handshake failed for https://wiz") from cause
    except HttpTransportError as exc:
        return exc


@pytest.mark.parametrize(
    ("error", "detail", "fix"),
    [
        (
            _untrusted(),
            "[SSL] handshake failed for https://wiz",
            ["config", "set", "untaped.http.ca_bundle_path", "<PATH>"],
        ),
        (
            HttpTransportError("cannot connect to https://wiz"),
            "cannot connect to https://wiz (wiz.server is url=https://wiz)",
            ["config", "set", "wiz.server.url", "<URL>"],
        ),
        (
            ValueError("unexpected shape"),
            "ValueError: unexpected shape (wiz.server is url=https://wiz)",
            ["config", "set", "wiz.server.url", "<URL>"],
        ),
    ],
)
def test_any_other_failure_names_the_first_marked_setting(
    _isolated_config: Path, error: Exception, detail: str, fix: list[str]
) -> None:
    check = online_check("wiz.api", section="wiz", probe=_raising(error))
    spec = make_spec("wiz", settings=WizProfile, doctor_checks=(check,))
    write_config(_isolated_config, f"format_version: 2\n{_SERVER}\n")
    _login()
    row = _doctor("--online", specs=(spec,), exit_code=1)["wiz.api:wiz"]
    assert (row["detail"], row["fix"][-len(fix) :]) == (detail, fix)


def test_a_failure_of_a_plugin_marking_only_its_secret_has_no_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOLO_TOKEN", "t")
    check = online_check("solo.api", section="solo", probe=_raising(HttpTransportError("down")))
    spec = make_spec(
        "solo", settings=SoloProfile, doctor_checks=(check,), credentials=(SOLO_TOKEN,)
    )
    rows = _doctor("--online", specs=(spec,), exit_code=1)
    assert rows["setup:solo"]["detail"] == "token from $SOLO_TOKEN"
    assert (rows["solo.api:solo"]["detail"], rows["solo.api:solo"]["fix"]) == ("down", None)


class HookServer(CredentialKey):
    """The server a ``hook`` signing key is for."""

    url: HttpUrl


class HookProfile(BaseModel):
    """A plugin whose one credential is not named ``token`` (section ``hook``)."""

    server: Annotated[HookServer | None, NeedsSetup] = None


SIGNING_KEY = Credential("hook", "signing_key", key=HookServer)


def _hook(*checks: Any) -> PluginSpec:
    return make_spec("hook", settings=HookProfile, doctor_checks=checks, credentials=(SIGNING_KEY,))


def test_a_credential_not_named_token_is_set_by_auth_login_with_its_name(
    _isolated_config: Path,
) -> None:
    write_config(_isolated_config, "format_version: 2\nhook: {server: {url: https://hook}}\n")
    row = _setup(specs=(_hook(),), plugin="hook")
    assert row["detail"] == "1 of 2 set; missing hook.signing_key"
    assert row["fix"][-4:] == ["auth", "login", "hook", "signing_key"]


def _plan_rows(*specs: PluginSpec) -> dict[str, dict[str, Any]]:
    result = _cli("setup", "plan", "--format", "json", specs=specs)
    assert result.exit_code == 0, result.output
    return {row["step"]: row for row in json.loads(result.stdout)}


class PinProfile(BaseModel):
    """A plugin needing a ``SecretStr`` it does not mark ``Secret`` (section ``pin``)."""

    pin: Annotated[SecretStr | None, NeedsSetup] = None


def test_an_unset_secret_value_is_the_users_step_even_without_a_secret_mark() -> None:
    row = _plan_rows(make_spec("pin", settings=PinProfile))["pin.pin"]
    assert (row["state"], row["by"]) == ("todo", "user")


def test_a_set_masked_value_is_shown_as_set(_isolated_config: Path) -> None:
    """``setup plan`` says ``set``, not the ``***`` a masked value prints as."""
    write_config(_isolated_config, "format_version: 2\npin: {pin: '1234'}\n")
    row = _plan_rows(make_spec("pin", settings=PinProfile))["pin.pin"]
    assert (row["state"], row["detail"]) == ("done", "set")


def test_a_failure_line_says_a_masked_value_is_set(_isolated_config: Path) -> None:
    check = online_check("pin.api", section="pin", probe=_raising(HttpTransportError("down")))
    spec = make_spec("pin", settings=PinProfile, doctor_checks=(check,))
    write_config(_isolated_config, "format_version: 2\npin: {pin: '1234'}\n")
    row = _doctor("--online", specs=(spec,), exit_code=1)["pin.api:pin"]
    assert row["detail"] == "down (pin.pin is set)"
    assert "1234" not in json.dumps(row)


def test_a_rejected_credential_not_named_token_names_its_own_auth_login(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rejected = HttpError("HTTP 401", status_code=401, url="https://hook/me", system="hook")
    check = online_check("hook.api", section="hook", probe=_raising(rejected))
    write_config(_isolated_config, "format_version: 2\nhook: {server: {url: https://hook}}\n")
    monkeypatch.setenv("UNTAPED_HOOK__SIGNING_KEY", "k")
    row = _doctor("--online", specs=(_hook(check),), exit_code=1)["hook.api:hook"]
    assert row["fix"][-4:] == ["auth", "login", "hook", "signing_key"]
