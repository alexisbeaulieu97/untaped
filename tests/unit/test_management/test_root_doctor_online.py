"""``doctor --online``: capability-contributed online checks and their fixes.

Online checks (``DoctorCheck(online=True)``) never run in a plain
``doctor``. The shared ``online_check`` factory probes a configured service
and names the ``untaped`` command that fixes a failure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, SecretStr

from test_management.support import GithubProfile, compose, make_spec, write_config
from untaped import bootstrap
from untaped.capability_api import (
    CapabilityContext,
    ConfigError,
    DoctorCheck,
    DoctorResult,
    HttpStatusError,
    HttpTransportError,
    TokenCommand,
    TokenSources,
    online_check,
)
from untaped.management.doctor import build_root_doctor_app
from untaped.testing import CliInvoker, CliResult

pytestmark = pytest.mark.usefixtures("_isolated_config")


class ProbeProfile(BaseModel):
    """Token-bearing profile double (section ``svc``)."""

    token_sources: ClassVar[TokenSources] = TokenSources(env=("SVC_TOKEN",))

    base_url: str | None = None
    token: SecretStr | None = None
    token_command: TokenCommand = None


def _doctor(checks: tuple[DoctorCheck, ...], *args: str) -> CliResult:
    spec = make_spec("svc", profile_model=ProbeProfile, doctor_checks=checks)
    app = build_root_doctor_app(shell=bootstrap.SHELL_SPEC, result=compose(spec))
    return CliInvoker().invoke(app, ["--format", "json", *args])


def _row(result: CliResult, check: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = json.loads(result.stdout)
    return next(row for row in rows if row["check"] == check)


def _configured(path: Path, **values: str) -> None:
    body = "".join(f"      {key}: {value}\n" for key, value in values.items())
    write_config(path, f"profiles:\n  default:\n    svc:\n{body}")


def test_plain_doctor_never_runs_online_checks(_isolated_config: Path) -> None:
    calls: list[str] = []

    def run(_ctx: CapabilityContext) -> DoctorResult:
        calls.append("ran")
        return DoctorResult(id="svc.api", ok=False, detail="down")

    check = DoctorCheck(id="svc.api", title="svc API", run=run, online=True)
    result = _doctor((check,))
    assert result.exit_code == 0, result.output
    assert calls == []
    assert all(row["check"] != "svc.api" for row in json.loads(result.stdout))


def test_online_failure_names_its_fix(_isolated_config: Path) -> None:
    def run(_ctx: CapabilityContext) -> DoctorResult:
        return DoctorResult(
            id="svc.api", ok=False, detail="token rejected", fix="config set svc.token --prompt"
        )

    check = DoctorCheck(id="svc.api", title="svc API", run=run, online=True)
    result = _doctor((check,), "--online")
    assert result.exit_code == 1
    row = _row(result, "svc.api")
    assert row["status"] == "fail"
    assert row["detail"] == "token rejected; run `untaped config set svc.token --prompt`"


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


def test_a_default_base_url_without_a_token_is_not_configured(_isolated_config: Path) -> None:
    def probe() -> str:
        raise AssertionError("must not probe")

    check = online_check("github.api", section="github", probe=probe)
    spec = make_spec("github", profile_model=GithubProfile, doctor_checks=(check,))
    app = build_root_doctor_app(shell=bootstrap.SHELL_SPEC, result=compose(spec))
    result = CliInvoker().invoke(app, ["--online", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert _row(result, "github.api")["detail"] == "not configured"


def _raise(exc: Exception) -> Any:
    def probe() -> str:
        raise exc

    return probe


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
            "svc rejected the configured token",
            "config set svc.token --prompt",
        ),
        (
            {"base_url": "https://svc"},
            HttpStatusError("HTTP 403 from https://svc/me", status_code=403),
            "HTTP 403 from https://svc/me",
            "config set svc.token --prompt",
        ),
        (
            {"base_url": "https://svc", "token": "t"},
            HttpTransportError("cannot connect to https://svc: name not resolved"),
            "cannot connect to https://svc: name not resolved",
            "config set svc.base_url URL",
        ),
        (
            {"base_url": "https://svc", "token": "t"},
            HttpTransportError("TLS error: CERTIFICATE_VERIFY_FAILED for https://svc"),
            "TLS error: CERTIFICATE_VERIFY_FAILED for https://svc",
            "config set http.ca_bundle PATH",
        ),
        (
            {"token": "t"},
            None,
            "svc.base_url is not set",
            "config set svc.base_url URL",
        ),
    ],
    ids=["rejected-token", "no-token", "unreachable", "tls", "no-base-url"],
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
    assert row["detail"] == f"{detail}; run `untaped {fix}`"


def test_online_check_is_skipped_when_settings_are_invalid(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    svc:\n      base_url: [1]\n")
    result = _doctor((_probe_check(_raise(AssertionError("must not probe"))),), "--online")
    assert _row(result, "svc.api")["detail"] == "skipped: settings are invalid"
