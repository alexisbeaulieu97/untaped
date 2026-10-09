"""``doctor --online``: plugin-contributed online checks and their fixes.

Online checks (``DoctorCheck(online=True)``) never run in a plain
``doctor``. The shared ``online_check`` factory probes a configured service
and names the ``untaped`` command that fixes a failure.
"""

from __future__ import annotations

import json
import ssl
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest
import respx
from pydantic import BaseModel, SecretStr

from test_management.support import GithubProfile, compose, make_spec, write_config
from untaped import bootstrap
from untaped.management.doctor import build_root_doctor_app
from untaped.profile_resolver import profile_scope
from untaped.sdk import (
    ConfigError,
    DoctorCheck,
    DoctorResult,
    HttpClient,
    HttpStatusError,
    HttpTransportError,
    PluginContext,
    RetryPolicy,
    TokenCommand,
    TokenSources,
    online_check,
)
from untaped.testing import CliInvoker, CliResult

pytestmark = pytest.mark.usefixtures("_isolated_config")


class ProbeProfile(BaseModel):
    """Token-bearing profile double (section ``svc``)."""

    token_sources: ClassVar[TokenSources] = TokenSources(env=("SVC_TOKEN",))

    base_url: str | None = None
    token: SecretStr | None = None
    token_command: TokenCommand = None


def _doctor(checks: tuple[DoctorCheck, ...], *args: str) -> CliResult:
    spec = make_spec("svc", settings=ProbeProfile, doctor_checks=checks)
    app = build_root_doctor_app(
        shell=bootstrap.SHELL_SPEC, builtin_for=lambda _name: None, result=compose(spec)
    )
    return CliInvoker().invoke(app, ["--format", "json", *args])


def _row(result: CliResult, check: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = json.loads(result.stdout)
    return next(row for row in rows if row["check"] == check)


def _configured(path: Path, **values: str) -> None:
    body = "".join(f"      {key}: {value}\n" for key, value in values.items())
    write_config(path, f"profiles:\n  default:\n    svc:\n{body}")


def test_plain_doctor_never_runs_online_checks(_isolated_config: Path) -> None:
    calls: list[str] = []

    def run(_ctx: PluginContext) -> DoctorResult:
        calls.append("ran")
        return DoctorResult(id="svc.api", ok=False, detail="down")

    check = DoctorCheck(id="svc.api", title="svc API", run=run, online=True)
    result = _doctor((check,))
    assert result.exit_code == 0, result.output
    assert calls == []
    assert all(row["check"] != "svc.api" for row in json.loads(result.stdout))


def test_online_failure_names_its_fix(_isolated_config: Path) -> None:
    def run(_ctx: PluginContext) -> DoctorResult:
        return DoctorResult(
            id="svc.api", ok=False, detail="token rejected", fix="config set svc.token --prompt"
        )

    check = DoctorCheck(id="svc.api", title="svc API", run=run, online=True)
    result = _doctor((check,), "--online")
    assert result.exit_code == 1
    row = _row(result, "svc.api")
    assert row["status"] == "fail"
    assert row["detail"] == "token rejected"
    assert row["fix"] == ["--profile", "default", "config", "set", "svc.token", "--prompt"]


def _probe_check(probe: Any) -> DoctorCheck:
    return online_check("svc.api", section="svc", probe=probe)


def test_online_check_passes_with_the_probe_detail(_isolated_config: Path) -> None:
    _configured(_isolated_config, base_url="https://svc", token="t")
    result = _doctor((_probe_check(lambda: "authenticated as alice"),), "--online")
    assert result.exit_code == 0, result.output
    row = _row(result, "svc.api")
    assert (row["status"], row["detail"]) == ("pass", "authenticated as alice")


def test_online_check_skips_an_unconfigured_service(_isolated_config: Path) -> None:
    def probe() -> str:
        raise AssertionError("must not probe")

    result = _doctor((_probe_check(probe),), "--online")
    assert result.exit_code == 0, result.output
    assert _row(result, "svc.api")["detail"] == "not configured"


def test_an_ambient_token_variable_without_a_base_url_is_not_configured(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SVC_TOKEN", "ambient")

    def probe() -> str:
        raise AssertionError("must not probe")

    result = _doctor((_probe_check(probe),), "--online")
    assert result.exit_code == 0, result.output
    assert _row(result, "svc.api")["detail"] == "not configured"


def test_a_default_base_url_without_a_token_is_not_configured(_isolated_config: Path) -> None:
    def probe() -> str:
        raise AssertionError("must not probe")

    check = online_check("github.api", section="github", probe=probe)
    spec = make_spec("github", settings=GithubProfile, doctor_checks=(check,))
    app = build_root_doctor_app(
        shell=bootstrap.SHELL_SPEC, builtin_for=lambda _name: None, result=compose(spec)
    )
    result = CliInvoker().invoke(app, ["--online", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert _row(result, "github.api")["detail"] == "not configured"


def _raise(exc: Exception) -> Any:
    def probe() -> str:
        raise exc

    return probe


def _tls(verify_code: int) -> Exception:
    """A transport error caused by an ``ssl`` certificate verification failure."""
    cause = ssl.SSLCertVerificationError(1, "certificate verify failed")
    cause.verify_code = verify_code
    try:
        raise cause
    except ssl.SSLCertVerificationError as err:
        try:
            raise HttpTransportError("[SSL] handshake failed for https://svc") from err
        except HttpTransportError as exc:
            return exc


def _rejected() -> Exception:
    try:
        raise HttpStatusError("HTTP 401 from https://svc/me", status_code=401)
    except HttpStatusError as cause:
        try:
            raise ConfigError("svc rejected the configured token\nhint: …") from cause
        except ConfigError as exc:
            return exc


@pytest.mark.parametrize(
    ("config", "error", "detail", "fix"),
    [
        (
            {"base_url": "https://svc", "token": "t"},
            _rejected(),
            "svc rejected the configured token "
            "(the token can also come from svc.token_command or $SVC_TOKEN)",
            "auth set svc",
        ),
        (
            {"base_url": "https://svc"},
            HttpStatusError("HTTP 403 from https://svc/me", status_code=403),
            "HTTP 403 from https://svc/me "
            "(the token can also come from svc.token_command or $SVC_TOKEN)",
            "auth set svc",
        ),
        (
            {"base_url": "https://svc", "token": "t"},
            HttpTransportError("cannot connect to https://svc: name not resolved"),
            "cannot connect to https://svc: name not resolved",
            "config set svc.base_url <URL>",
        ),
        (
            {"base_url": "https://svc"},
            HttpTransportError("cannot connect to https://svc: connection refused"),
            "cannot connect to https://svc: connection refused",
            "config set svc.base_url <URL>",
        ),
        (
            {"base_url": "https://svc", "token": "t"},
            _tls(20),  # X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT_LOCALLY
            "[SSL] handshake failed for https://svc",
            "config set http.ca_bundle <PATH>",
        ),
        (
            {"base_url": "https://svc", "token": "t"},
            _tls(62),  # X509_V_ERR_HOSTNAME_MISMATCH: the URL names the wrong host
            "[SSL] handshake failed for https://svc",
            "config set svc.base_url <URL>",
        ),
        (
            {"base_url": "https://svc", "token": "t"},
            HttpTransportError("certificate problem mentioned, but no ssl cause"),
            "certificate problem mentioned, but no ssl cause",
            "config set svc.base_url <URL>",
        ),
        (
            {"base_url": "https://svc", "token": "t"},
            ValueError("1 validation error for SvcUser\nlogin\n  input_value={'body': 'secret'}"),
            "ValueError: 1 validation error for SvcUser",
            "config set svc.base_url <URL>",
        ),
        (
            {"token": "t"},
            None,
            "svc.base_url is not set",
            "config set svc.base_url <URL>",
        ),
    ],
    ids=[
        "rejected-token",
        "no-token",
        "unreachable",
        "unreachable-without-token",
        "tls-untrusted",
        "tls-hostname",
        "certificate-word-only",
        "unexpected-response",
        "no-base-url",
    ],
)
def test_online_check_failures_name_the_fix(
    _isolated_config: Path,
    config: dict[str, str],
    error: Exception | None,
    detail: str,
    fix: str,
) -> None:
    _configured(_isolated_config, **config)
    probe = _raise(error or AssertionError("must not probe"))
    result = _doctor((_probe_check(probe),), "--online")
    assert result.exit_code == 1
    row = _row(result, "svc.api")
    assert row["status"] == "fail"
    assert row["detail"] == detail
    assert row["fix"] == ["--profile", "default", *fix.split()]


def test_online_probes_do_not_retry_and_use_a_short_timeout(_isolated_config: Path) -> None:
    _configured(_isolated_config, base_url="https://svc", token="t")
    timeouts: list[object] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"]["connect"])
        raise httpx.ConnectError("connection refused", request=request)

    def probe() -> str:
        client = HttpClient("https://svc", timeout=30.0, retry=RetryPolicy(backoff_base=0))
        client.request("GET", "/me")
        return "unreachable"

    with respx.mock(base_url="https://svc") as mock:
        route = mock.get("/me").mock(side_effect=refuse)
        result = _doctor((_probe_check(probe),), "--online")
    assert _row(result, "svc.api")["status"] == "fail"
    assert route.call_count == 1
    assert timeouts == [10.0]


def test_online_check_is_skipped_when_settings_are_invalid(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    svc:\n      base_url: [1]\n")
    result = _doctor((_probe_check(_raise(AssertionError("must not probe"))),), "--online")
    assert _row(result, "svc.api")["detail"] == "skipped: settings are invalid"


def _fixing(fix: str | list[str]) -> DoctorCheck:
    def run(_ctx: PluginContext) -> DoctorResult:
        return DoctorResult(id="svc.api", ok=False, detail="token rejected", fix=fix)

    return DoctorCheck(id="svc.api", title="svc API", run=run, online=True)


@pytest.mark.parametrize(
    "fix", ["untaped config set svc.base_url '<URL>'", ["config", "set", "svc.base_url", "<URL>"]]
)
def test_a_string_or_argv_fix_becomes_the_same_argv(_isolated_config: Path, fix: Any) -> None:
    row = _row(_doctor((_fixing(fix),), "--online"), "svc.api")
    assert row["fix"] == ["--profile", "default", "config", "set", "svc.base_url", "<URL>"]


def test_a_fix_that_names_a_profile_keeps_only_its_own(_isolated_config: Path) -> None:
    row = _row(_doctor((_fixing("--profile=prod auth set svc"),), "--online"), "svc.api")
    assert row["fix"] == ["--profile=prod", "auth", "set", "svc"]


def test_an_unparsable_fix_fails_its_row(_isolated_config: Path) -> None:
    row = _row(_doctor((_fixing("config set 'svc.base_url"),), "--online"), "svc.api")
    assert row["status"] == "fail"
    assert row["detail"].startswith("check returned an unparsable fix")
    assert row["fix"] is None


def test_the_checklist_shows_the_fix_without_the_current_profile(_isolated_config: Path) -> None:
    spec = make_spec("svc", settings=ProbeProfile, doctor_checks=(_fixing("auth set svc"),))
    app = build_root_doctor_app(
        shell=bootstrap.SHELL_SPEC, builtin_for=lambda _name: None, result=compose(spec)
    )
    result = CliInvoker().invoke(app, ["--format", "table", "--online"])
    assert "token rejected\n" in result.stdout
    assert "→ untaped auth set svc\n" in result.stdout


def test_the_table_keeps_a_profile_the_flag_chose(_isolated_config: Path) -> None:
    spec = make_spec("svc", settings=ProbeProfile, doctor_checks=(_fixing("auth set svc"),))
    app = build_root_doctor_app(
        shell=bootstrap.SHELL_SPEC, builtin_for=lambda _name: None, result=compose(spec)
    )
    with profile_scope("default"):
        result = CliInvoker().invoke(app, ["--format", "table", "--online"])
    # Without the flag the same line would act on the configured active profile.
    assert "→ untaped --profile default auth set svc\n" in result.stdout
