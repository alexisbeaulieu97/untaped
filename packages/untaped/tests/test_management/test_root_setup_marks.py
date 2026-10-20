"""Core's ``setup`` doctor row and the set rules, read from a plugin's setting marks.

One reading of "set" serves doctor, ``setup plan`` and the online checks: a
value from the active profile, the ``default`` profile it inherits, an
``UNTAPED_*`` variable (a leaf or a JSON object) counts; a blank string does
not, ``[]`` does. A secret's conventional variable is ambient: it counts
only once every other marked field is set. A ``token_command`` source counts
without running.
"""

from __future__ import annotations

import json
import ssl
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import pytest
from pydantic import BaseModel, SecretStr

from test_management.stores import install_fake_stores
from test_management.support import (
    FAIL,
    PROBES,
    ExtProfile,
    UnmarkedWizProfile,
    WizProfile,
    make_spec,
    wiz_api_check,
    write_config,
)
from untaped import bootstrap
from untaped.plugins.registry import PluginSpec
from untaped.sdk import HttpTransportError, NeedsSetup, Secret, connection_check, online_check
from untaped.stability import forget_notices
from untaped.testing import CliResult, invoke_cli, plugin_candidate

pytestmark = pytest.mark.usefixtures("_isolated_config")


class CrewProfile(BaseModel):
    """A plugin whose one marked field is a list (section ``crew``)."""

    teams: Annotated[list[str] | None, NeedsSetup] = None


class SoloProfile(BaseModel):
    """A plugin whose one marked field is its secret (section ``solo``)."""

    token: Annotated[SecretStr | None, NeedsSetup, Secret(env=("SOLO_TOKEN",))] = None


@pytest.fixture(autouse=True)
def _clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No store, no ambient token, a fresh once-per-run warning memory."""
    PROBES.clear()
    FAIL.clear()
    forget_notices()
    install_fake_stores(tmp_path, monkeypatch)
    for name in ("WIZ_TOKEN", "UNTAPED_PROFILE", "UNTAPED_WIZ", "UNTAPED_WIZ__BASE_URL"):
        monkeypatch.delenv(name, raising=False)


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
    write_config(_isolated_config, "profiles:\n  default:\n    wiz: {base_url: https://wiz}\n")
    row = _setup()
    assert row["status"] == "warn"
    assert row["detail"] == "1 of 2 set; missing wiz.token"
    assert row["fix"][-3:] == ["auth", "set", "wiz"]


def test_a_missing_plain_field_names_config_set_with_its_placeholder(
    _isolated_config: Path,
) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    wiz: {token_command: [x]}\n")
    row = _setup()
    assert row["detail"] == "1 of 2 set; missing wiz.base_url"
    assert row["fix"][-4:] == ["config", "set", "wiz.base_url", "<BASE_URL>"]


def test_a_plaintext_token_warns_with_the_automatic_auth_migrate(_isolated_config: Path) -> None:
    write_config(
        _isolated_config, "profiles:\n  default:\n    wiz: {base_url: https://wiz, token: t}\n"
    )
    row = _setup()
    assert row["status"] == "warn"
    assert row["detail"].startswith("https://wiz; token from wiz.token; ")
    assert row["fix"][-2:] == ["auth", "migrate"]
    assert row["automatic"] is True


@pytest.mark.parametrize(
    ("config", "env", "profile"),
    [
        pytest.param(
            "  default:\n    wiz: {base_url: https://wiz, token_command: [x]}\n",
            {},
            None,
            id="profile",
        ),
        pytest.param(
            "  default:\n    wiz: {base_url: https://wiz}\n"
            "  work:\n    wiz: {token_command: [x]}\n",
            {},
            "work",
            id="default-profile",
        ),
        pytest.param(
            "  default: {}\n",
            {"UNTAPED_WIZ__BASE_URL": "https://wiz", "UNTAPED_WIZ__TOKEN_COMMAND": '["x"]'},
            None,
            id="leaf-variable",
        ),
        pytest.param(
            "  default: {}\n",
            {"UNTAPED_WIZ": '{"base_url": "https://wiz", "token_command": ["x"]}'},
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
    write_config(_isolated_config, f"profiles:\n{config}")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    args = ("--profile", profile) if profile else ()
    result = _cli(*args, "doctor", "--format", "json")
    row = next(r for r in json.loads(result.stdout) if r["check"] == "setup")
    assert (row["status"], row["detail"]) == ("pass", "https://wiz; token from wiz.token_command")


def test_a_blank_string_is_not_set(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    wiz: {base_url: '  ', token_command: [x]}\n",
    )
    assert _setup()["detail"] == "1 of 2 set; missing wiz.base_url"


def test_an_empty_list_is_set(_isolated_config: Path) -> None:
    crew = make_spec("crew", settings=CrewProfile)
    write_config(_isolated_config, "profiles:\n  default:\n    crew: {teams: []}\n")
    row = _setup(specs=(crew,), plugin="crew")
    assert (row["status"], row["detail"]) == ("pass", "[]")


def test_an_ambient_variable_alone_does_not_set_up_a_plugin_with_other_marks(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WIZ_TOKEN", "ambient")
    assert _setup()["detail"] == "not set up"
    write_config(_isolated_config, "profiles:\n  default:\n    wiz: {base_url: https://wiz}\n")
    row = _setup()
    assert (row["status"], row["detail"]) == ("pass", "https://wiz; token from $WIZ_TOKEN")


def test_a_token_command_counts_without_running(_isolated_config: Path, tmp_path: Path) -> None:
    ran = tmp_path / "ran"
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    wiz:\n      base_url: https://wiz\n"
        f"      token_command: [sh, -c, 'touch {ran}; exit 1']\n",
    )
    assert _setup()["status"] == "pass"
    assert not ran.exists()


def test_every_marked_field_set_runs_the_online_check(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    wiz: {base_url: https://wiz, token_command: [echo, t]}\n",
    )
    rows = _doctor("--online")
    assert rows["wiz.api:wiz"]["status"] == "pass"
    assert PROBES == ["probed"]


def test_the_online_check_waits_for_every_marked_field(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    wiz: {base_url: https://wiz}\n")
    rows = _doctor("--online")
    assert rows["wiz.api:wiz"]["detail"] == "skipped: not set up"
    assert PROBES == []


def test_a_rejected_token_names_auth_set(_isolated_config: Path) -> None:
    FAIL.append(True)
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    wiz: {base_url: https://wiz, token_command: [echo, t]}\n",
    )
    row = _doctor("--online", exit_code=1)["wiz.api:wiz"]
    assert row["status"] == "fail"
    assert row["fix"][-3:] == ["auth", "set", "wiz"]
    assert "the token can also come from" in row["detail"]


def _legacy() -> PluginSpec:
    return make_spec(
        "wiz",
        settings=UnmarkedWizProfile,
        doctor_checks=(connection_check("wiz.connection", section="wiz"),),
    )


def test_an_unmarked_plugin_keeps_its_rows_and_warns_once(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    wiz: {base_url: https://wiz}\n")
    result = _cli("doctor", "--format", "json", specs=(_legacy(),))
    rows = {row["check"]: row for row in json.loads(result.stdout)}
    assert "setup" not in rows
    assert rows["wiz.connection"]["detail"].startswith("wiz.token not configured")
    assert result.stderr.count("behaviour untaped.unmarked-service") == 1
    plan = _cli("setup", "plan", "--format", "json", specs=(_legacy(),))
    base_url = next(row for row in json.loads(plan.stdout) if row["step"] == "wiz.base_url")
    assert base_url["state"] == "done"
    token = next(row for row in json.loads(plan.stdout) if row["step"] == "wiz.token")
    assert token["state"] == "todo"


def test_the_fallback_warns_only_where_setup_is_read() -> None:
    result = _cli("config", "list", "--format", "json", specs=(_legacy(),))
    assert result.exit_code == 0, result.output
    assert "NeedsSetup" not in result.stderr


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
            ["config", "set", "http.ca_bundle", "<PATH>"],
        ),
        (
            HttpTransportError("cannot connect to https://wiz"),
            "cannot connect to https://wiz (wiz.base_url is https://wiz)",
            ["config", "set", "wiz.base_url", "<BASE_URL>"],
        ),
        (
            ValueError("unexpected shape"),
            "ValueError: unexpected shape (wiz.base_url is https://wiz)",
            ["config", "set", "wiz.base_url", "<BASE_URL>"],
        ),
    ],
)
def test_any_other_failure_names_the_first_marked_setting(
    _isolated_config: Path, error: Exception, detail: str, fix: list[str]
) -> None:
    check = online_check("wiz.api", section="wiz", probe=_raising(error))
    spec = make_spec("wiz", settings=WizProfile, doctor_checks=(check,))
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    wiz: {base_url: https://wiz, token_command: [echo, t]}\n",
    )
    row = _doctor("--online", specs=(spec,), exit_code=1)["wiz.api:wiz"]
    assert (row["detail"], row["fix"][-len(fix) :]) == (detail, fix)


def test_a_failure_of_a_plugin_marking_only_its_secret_has_no_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOLO_TOKEN", "t")
    check = online_check("solo.api", section="solo", probe=_raising(HttpTransportError("down")))
    spec = make_spec("solo", settings=SoloProfile, doctor_checks=(check,))
    rows = _doctor("--online", specs=(spec,), exit_code=1)
    assert rows["setup:solo"]["detail"] == "token from $SOLO_TOKEN"
    assert (rows["solo.api:solo"]["detail"], rows["solo.api:solo"]["fix"]) == ("down", None)


class HookProfile(BaseModel):
    """A plugin whose one secret is not named ``token`` (section ``hook``)."""

    url: Annotated[str | None, NeedsSetup] = None
    signing_key: Annotated[SecretStr | None, NeedsSetup, Secret()] = None


def test_a_secret_not_named_token_is_set_by_its_command_setting(
    _isolated_config: Path,
) -> None:
    hook = make_spec("hook", settings=HookProfile)
    write_config(_isolated_config, "profiles:\n  default:\n    hook: {url: https://hook}\n")
    row = _setup(specs=(hook,), plugin="hook")
    assert row["detail"] == "1 of 2 set; missing hook.signing_key"
    assert row["fix"][-4:] == ["config", "set", "hook.signing_key_command", "<COMMAND>"]
    refused = _cli("auth", "set", "hook", specs=(hook,))
    assert refused.exit_code != 0
    assert "set hook.signing_key_command to a command that prints it" in refused.stderr
