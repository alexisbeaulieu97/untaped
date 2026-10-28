"""``doctor --online``: plugin-contributed online checks and their fixes.

Online checks (``DoctorCheck(online=True)``) never run in a plain
``doctor``. The shared ``OnlineCheck`` block probes a configured service
and names the ``untaped`` command that fixes a failure.
"""

from __future__ import annotations

import json
import ssl
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import httpx
import pytest
import respx
from pydantic import BaseModel, HttpUrl

from core_test_support.commands.support import (
    make_plugin,
    root_group,
    write_config,
)
from untaped.sdk import (
    ConfigError,
    Credential,
    CredentialKey,
    DoctorCheck,
    DoctorResult,
    HttpClient,
    HttpStatusError,
    HttpTransportError,
    NeedsSetup,
    OnlineCheck,
    Plugin,
    PluginContext,
    RetryPolicy,
)
from untaped.sdk.testing import CliInvoker, CliResult

pytestmark = pytest.mark.usefixtures("_isolated_config")


class SvcServer(CredentialKey):
    """The server an ``svc`` token is for."""

    url: HttpUrl


class ProbeProfile(BaseModel):
    """Service double (section ``svc``): a server and a declared token."""

    server: Annotated[SvcServer | None, NeedsSetup] = None


def _spec(api: DoctorCheck) -> type[Plugin]:
    """``svc`` checking ``api``, with its token read last from ``$SVC_TOKEN``."""
    token = Credential(key=SvcServer, env=("SVC_TOKEN",))
    return make_plugin("svc", settings=ProbeProfile, token=token, api=api)


def _doctor(api: DoctorCheck, *args: str) -> CliResult:
    spec = _spec(api)
    app = root_group("doctor", spec)
    return CliInvoker().invoke(app, ["--format", "json", *args])


def _row(result: CliResult, check: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = json.loads(result.stdout)
    return next(row for row in rows if row["check"] == check)


@pytest.fixture
def _configured(_isolated_config: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Set ``svc``'s server ``url`` in the config and its ``token`` in ``$SVC_TOKEN``."""

    def configure(*, url: str | None = None, token: str | None = None) -> None:
        if token is not None:
            monkeypatch.setenv("SVC_TOKEN", token)
        server = "" if url is None else f"  server: {{url: {url}}}\n"
        write_config(_isolated_config, f"format_version: 2\nsvc:\n{server}")

    return configure


def test_plain_doctor_never_runs_online_checks(_isolated_config: Path) -> None:
    calls: list[str] = []

    def run(_ctx: PluginContext) -> DoctorResult:
        calls.append("ran")
        return DoctorResult(id="svc.api", ok=False, detail="down")

    check = DoctorCheck(title="svc API", run=run, online=True)
    result = _doctor(check)
    assert result.exit_code == 0, result.output
    assert calls == []
    assert all(row["check"] != "svc.api" for row in json.loads(result.stdout))


def test_online_failure_names_its_fix(_isolated_config: Path) -> None:
    def run(_ctx: PluginContext) -> DoctorResult:
        return DoctorResult(
            id="svc.api", ok=False, detail="token rejected", fix="config set svc.token --prompt"
        )

    check = DoctorCheck(title="svc API", run=run, online=True)
    result = _doctor(check, "--online")
    assert result.exit_code == 1
    row = _row(result, "svc.api")
    assert row["status"] == "fail"
    assert row["detail"] == "token rejected"
    assert row["fix"] == ["--profile", "default", "config", "set", "svc.token", "--prompt"]


def _probe_check(probe: Any) -> DoctorCheck:
    return OnlineCheck(probe=probe)


def test_online_check_passes_with_the_probe_detail(_configured: Callable[..., None]) -> None:
    _configured(url="https://svc", token="t")
    result = _doctor(_probe_check(lambda: "authenticated as alice"), "--online")
    assert result.exit_code == 0, result.output
    row = _row(result, "svc.api")
    assert (row["status"], row["detail"]) == ("pass", "authenticated as alice")


def test_online_check_skips_an_unconfigured_service(_isolated_config: Path) -> None:
    def probe() -> str:
        raise AssertionError("must not probe")

    result = _doctor(_probe_check(probe), "--online")
    assert result.exit_code == 0, result.output
    assert _row(result, "svc.api")["detail"] == "skipped: not set up"


def test_an_ambient_token_variable_without_a_server_is_not_set_up(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SVC_TOKEN", "ambient")

    def probe() -> str:
        raise AssertionError("must not probe")

    result = _doctor(_probe_check(probe), "--online")
    assert result.exit_code == 0, result.output
    assert _row(result, "svc.api")["detail"] == "skipped: not set up"


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
            {"url": "https://svc", "token": "t"},
            _rejected(),
            "svc rejected the configured token (the token can also come from $SVC_TOKEN)",
            "auth login svc",
        ),
        (
            {"url": "https://svc", "token": "t"},
            HttpTransportError("cannot connect to https://svc: name not resolved"),
            "cannot connect to https://svc: name not resolved (svc.server is url=https://svc)",
            "config set svc.server.url <URL>",
        ),
        (
            {"url": "https://svc", "token": "t"},
            _tls(20),  # X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT_LOCALLY
            "[SSL] handshake failed for https://svc",
            "config set untaped.http.ca_bundle_path <PATH>",
        ),
        (
            {"url": "https://svc", "token": "t"},
            _tls(62),  # X509_V_ERR_HOSTNAME_MISMATCH: the URL names the wrong host
            "[SSL] handshake failed for https://svc (svc.server is url=https://svc)",
            "config set svc.server.url <URL>",
        ),
        (
            {"url": "https://svc", "token": "t"},
            HttpTransportError("certificate problem mentioned, but no ssl cause"),
            "certificate problem mentioned, but no ssl cause (svc.server is url=https://svc)",
            "config set svc.server.url <URL>",
        ),
        (
            {"url": "https://svc", "token": "t"},
            ValueError("1 validation error for SvcUser\nlogin\n  input_value={'body': 'secret'}"),
            "ValueError: 1 validation error for SvcUser (svc.server is url=https://svc)",
            "config set svc.server.url <URL>",
        ),
    ],
    ids=[
        "rejected-token",
        "unreachable",
        "tls-untrusted",
        "tls-hostname",
        "certificate-word-only",
        "unexpected-response",
    ],
)
def test_online_check_failures_name_the_fix(
    _configured: Callable[..., None],
    config: dict[str, str],
    error: Exception,
    detail: str,
    fix: str,
) -> None:
    _configured(**config)
    probe = _raise(error)
    result = _doctor(_probe_check(probe), "--online")
    assert result.exit_code == 1
    row = _row(result, "svc.api")
    assert row["status"] == "fail"
    assert row["detail"] == detail
    assert row["fix"] == ["--profile", "default", *fix.split()]


def test_online_probes_do_not_retry_and_use_a_short_timeout(
    _configured: Callable[..., None],
) -> None:
    _configured(url="https://svc", token="t")
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
        result = _doctor(_probe_check(probe), "--online")
    assert _row(result, "svc.api")["status"] == "fail"
    assert route.call_count == 1
    assert timeouts == [10.0]


def test_online_check_is_skipped_when_settings_are_invalid(_isolated_config: Path) -> None:
    write_config(_isolated_config, "format_version: 2\nsvc:\n  server: [1]\n")
    result = _doctor(_probe_check(_raise(AssertionError("must not probe"))), "--online")
    assert _row(result, "svc.api")["detail"] == "skipped: settings are invalid"


def _fixing(fix: str | list[str]) -> DoctorCheck:
    def run(_ctx: PluginContext) -> DoctorResult:
        return DoctorResult(id="svc.api", ok=False, detail="token rejected", fix=fix)

    return DoctorCheck(title="svc API", run=run, online=True)


@pytest.mark.parametrize(
    "fix",
    ["untaped config set svc.server.url '<URL>'", ["config", "set", "svc.server.url", "<URL>"]],
)
def test_a_string_or_argv_fix_becomes_the_same_argv(_isolated_config: Path, fix: Any) -> None:
    row = _row(_doctor(_fixing(fix), "--online"), "svc.api")
    assert row["fix"] == ["--profile", "default", "config", "set", "svc.server.url", "<URL>"]


def test_a_fix_that_names_a_profile_keeps_only_its_own(_isolated_config: Path) -> None:
    row = _row(_doctor(_fixing("--profile=prod auth login svc"), "--online"), "svc.api")
    assert row["fix"] == ["--profile=prod", "auth", "login", "svc"]


def test_an_unparsable_fix_fails_its_row(_isolated_config: Path) -> None:
    row = _row(_doctor(_fixing("config set 'svc.server.url"), "--online"), "svc.api")
    assert row["status"] == "fail"
    assert row["detail"].startswith("check returned an unparsable fix")
    assert row["fix"] is None


def test_the_checklist_shows_the_fix_without_the_current_profile(_isolated_config: Path) -> None:
    spec = _spec(_fixing("auth login svc"))
    app = root_group("doctor", spec)
    result = CliInvoker().invoke(app, ["--format", "table", "--online"])
    assert "token rejected\n" in result.stdout
    assert "→ untaped auth login svc\n" in result.stdout


def test_the_table_keeps_a_profile_the_flag_chose(_isolated_config: Path) -> None:
    spec = _spec(_fixing("auth login svc"))
    app = root_group("doctor", spec)
    result = CliInvoker().invoke(app, ["--profile", "default", "--format", "table", "--online"])
    # Without the flag the same line would read the profiles UNTAPED_PROFILE picks.
    assert "→ untaped --profile default auth login svc\n" in result.stdout


@pytest.mark.parametrize(
    ("env", "flag"), [("a,b", None), (None, ("a", "b")), ("a", ("b",)), ("b", None)]
)
def test_a_fix_writes_to_the_top_of_a_picked_stack(
    _isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    env: str | None,
    flag: tuple[str, ...] | None,
) -> None:
    write_config(_isolated_config, "format_version: 2\nprofiles:\n  a: {}\n  b: {}\n")
    if env:
        monkeypatch.setenv("UNTAPED_PROFILE", env)
    spec = _spec(_fixing("auth login svc"))
    app = root_group("doctor", spec)
    picked = ["--profile", ",".join(flag)] if flag else []
    listed = CliInvoker().invoke(app, [*picked, "--format", "table", "--online"])
    row = _row(CliInvoker().invoke(app, [*picked, "--format", "json", "--online"]), "svc.api")

    # One profile, so a write lands where this run reads it; kept on the line,
    # since the bare line would write to the base.
    assert row["fix"] == ["--profile", "b", "auth", "login", "svc"]
    assert "→ untaped --profile b auth login svc\n" in listed.stdout


def test_a_base_fix_keeps_its_profile_when_the_variable_picks(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, "format_version: 2\nprofiles:\n  a: {}\n")
    monkeypatch.setenv("UNTAPED_PROFILE", "a")
    spec = _spec(_fixing("auth login svc"))
    app = root_group("doctor", spec)
    # `--profile default` sets the variable aside
    result = CliInvoker().invoke(app, ["--profile", "default", "--format", "table", "--online"])

    # The bare line would read profile `a` again.
    assert "→ untaped --profile default auth login svc\n" in result.stdout
