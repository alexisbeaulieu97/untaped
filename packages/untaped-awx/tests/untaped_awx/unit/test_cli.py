import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from awx_test_support import Settings, run
from untaped.sdk.testing import CliResult


def _mock_me(mock: respx.Router, path: str = "/api/v2/me/") -> None:
    """``ping`` also authenticates through ``/me/``."""
    mock.get(path).mock(return_value=httpx.Response(200, json={"results": [{"username": "admin"}]}))


@pytest.mark.parametrize(
    ("api_prefix", "expected_path"),
    [
        (None, "/api/controller/v2/ping/"),  # AAP default
        ("/api/v2/", "/api/v2/ping/"),  # upstream AWX
    ],
)
def test_ping_uses_configured_api_prefix(
    settings: Settings, api_prefix: str | None, expected_path: str
) -> None:
    if api_prefix is None:
        del settings["api_prefix"]

    with respx.mock(base_url="https://aap.example.com") as mock:
        _mock_me(mock, expected_path.replace("ping/", "me/"))
        mock.get(expected_path).mock(
            return_value=httpx.Response(
                200,
                json={"version": "4.5.0", "active_node": "controller-1"},
            )
        )
        result = run(["ping", "--format", "raw", "--columns", "version"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "4.5.0"


def test_ping_table_renders_detail_view() -> None:
    """A single entity renders as a vertical key:value detail view under the
    default config — not a boxed one-row table (the ``emit`` single-record
    contract). Before the migration this default-config render was a grid."""

    with respx.mock(base_url="https://aap.example.com") as mock:
        _mock_me(mock)
        mock.get("/api/v2/ping/").mock(
            return_value=httpx.Response(
                200,
                json={"version": "4.5.0", "active_node": "controller-1"},
            )
        )
        result = run(["ping", "--format", "table"])

    assert result.exit_code == 0, result.output
    assert "version: 4.5.0" in result.stdout
    assert "active_node: controller-1" in result.stdout
    assert "user: admin" in result.stdout
    assert not any(ch in result.stdout for ch in "╭╮╰╯┌┐└┘│─")


def test_ping_json_emits_bare_object() -> None:
    """``ping --format json`` emits a bare object ``{…}`` (not ``[{…}]``)."""

    with respx.mock(base_url="https://aap.example.com") as mock:
        _mock_me(mock)
        mock.get("/api/v2/ping/").mock(
            return_value=httpx.Response(200, json={"version": "4.5.0", "active_node": "c-1"})
        )
        result = run(["ping", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    assert payload["version"] == "4.5.0"


def test_ping_fails_when_the_token_is_rejected() -> None:
    """``/ping/`` is unauthenticated; ping must also prove the token works."""

    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/ping/").mock(
            return_value=httpx.Response(200, json={"version": "4.5.0", "active_node": "c-1"})
        )
        mock.get("/api/v2/me/").mock(
            return_value=httpx.Response(401, json={"detail": "Invalid token."})
        )
        result = run(["ping"])

    assert result.exit_code != 0
    assert "401" in result.output + (result.stderr or "")


def _list_answering(response: httpx.Response, *args: str) -> CliResult:
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/job_templates/").mock(return_value=response)
        return run(["job-templates", "list", *args])


def test_a_rejected_token_exits_4_with_the_token_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rejected = httpx.Response(401, json={"detail": "Invalid token."})
    result = _list_answering(rejected)

    assert result.exit_code == 4
    assert result.stderr.endswith(
        "error: AWX rejected the token (HTTP 401)\nhint (your step): run `untaped auth login awx`\n"
    )


def _last_diagnostic(result: CliResult) -> dict[str, Any]:
    record: dict[str, Any] = json.loads(result.stderr.splitlines()[-1])
    return record


def test_json_diagnostics_report_the_failure_attribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    rejected = httpx.Response(401, json={"detail": "Invalid token."})
    result = _list_answering(rejected)

    assert result.exit_code == 4
    record = _last_diagnostic(result)
    assert record["message"] == "AWX rejected the token (HTTP 401)"
    assert (record["category"], record["system"]) == ("auth", "awx")
    assert record["hint"] == "run `untaped auth login awx`"
    assert record["details"]["status"] == 401


@pytest.mark.parametrize(
    ("status", "category", "exit_code"),
    [(502, "unavailable", 5), (403, "permission", 4), (400, "invalid", 1)],
)
def test_an_awx_failure_exits_with_its_category(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    category: str,
    exit_code: int,
) -> None:
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    failure = httpx.Response(status, json={"detail": "nope"})
    result = _list_answering(failure)

    assert result.exit_code == exit_code
    record = _last_diagnostic(result)
    assert (record["category"], record["system"]) == (category, "awx")


def test_ping_rejects_command_local_profile_flag() -> None:
    """Profile selection is built into the SDK.

    Commands do not expose a local ``--profile``, so passing one
    directly to the tool app is an unknown-option error (exit 2).
    """

    result = run(
        ["ping", "--profile", "stage", "--format", "raw", "--columns", "version"],
    )

    assert result.exit_code == 2, result.output


def test_ping_requires_base_url(settings: Settings) -> None:
    del settings["server"]
    result = run(["ping"])
    assert result.exit_code != 0
    assert "awx.server is not set" in result.output


@pytest.mark.parametrize("cli_name", ["organizations", "credential-types", "job-templates"])
def test_list_applies_default_organization_only_to_scoped_kinds(
    settings: Settings, cli_name: str
) -> None:
    """The default organization scopes applicable resource queries; global kinds remain global."""
    settings["default_organization"] = "Default"

    captured: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={"count": 0, "next": None, "previous": None, "results": []},
        )

    api_path = {
        "credential-types": "credential_types",
        "job-templates": "job_templates",
    }.get(cli_name, cli_name)
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get(f"/api/v2/{api_path}/").mock(side_effect=_record)
        result = run([cli_name, "list", "--format", "raw"])

    assert result.exit_code == 0, result.output
    assert captured, "no request captured"
    for req in captured:
        assert ("organization__name" in req.url.params) == (cli_name == "job-templates")


def test_list_filter_passes_through_to_awx() -> None:
    """``--filter KEY=VALUE`` must reach AWX as a verbatim URL param so any
    Django-style lookup (``__name``, ``__icontains``, ``__contains``,
    exact match, …) works without code changes."""

    captured: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"count": 0, "next": None, "previous": None, "results": []})

    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/job_templates/").mock(side_effect=_record)
        result = run(
            [
                "job-templates",
                "list",
                "--filter",
                "organization__name=Default",
                "--filter",
                "name__icontains=deploy",
                "--format",
                "raw",
            ],
        )

    assert result.exit_code == 0, result.output
    assert captured
    params = captured[-1].url.params
    assert params.get("organization__name") == "Default"
    assert params.get("name__icontains") == "deploy"


def test_list_filter_rejects_malformed_entry() -> None:
    """A malformed ``--filter`` (no ``=``) must fail up front — silently
    posting it to AWX surfaces as an opaque HTTP 400."""

    result = run(["job-templates", "list", "--filter", "bogus"])
    assert result.exit_code != 0
    output = result.output + (result.stderr or "")
    assert "KEY=VALUE" in output


def test_get_bare_invocation_is_usage_error_without_opening_context() -> None:
    result = run(["job-templates", "get"])

    assert result.exit_code == 2
    assert "error: provide names, --stdin, filters/search, or --all" in result.stderr
    assert "awx.server" not in result.output


def test_apply_rejects_parallel_below_one_before_opening_context(tmp_path: Path) -> None:
    yml = tmp_path / "empty.yml"
    yml.write_text("")

    result = run(["apply", str(yml), "--parallel", "0"])

    assert result.exit_code != 0
    assert "parallel" in result.output
    assert "awx.server" not in result.output


def test_apply_emits_clamp_warning_above_cap(tmp_path: Path) -> None:
    """``awx apply --parallel`` above the cap stays accepted (clamped)
    but a stderr warning surfaces the truncation so users notice when
    they ask for more concurrency than they get. Without this test the
    warning could silently regress to a no-op."""
    yml = tmp_path / "empty.yml"
    yml.write_text("")  # zero docs → no AWX calls, runner just prints rows
    result = run(["apply", str(yml), "--parallel", "100"])
    assert result.exit_code == 0, result.output
    assert "clamped to 10" in result.output
    assert "(this command's cap)" in result.output


@pytest.mark.parametrize(
    "template",
    [
        pytest.param(["apply", "FILE"], id="top-level-positional"),
    ],
)
def test_apply_accepts_positional_file(tmp_path: Path, template: list[str]) -> None:
    """Apply commands take the YAML file as a required positional argument."""
    yml = tmp_path / "empty.yml"
    yml.write_text("")
    args = [str(yml) if a == "FILE" else a for a in template]
    result = run(args)
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("command", [["apply"]])
def test_apply_takes_a_positional_file_and_advertises_parallel(command: list[str]) -> None:
    """Apply takes a positional file; ``--file`` stays removed."""
    helped = run([*command, "--help"])
    assert helped.exit_code == 0
    assert "YAML file" in helped.output
    assert "--parallel" in helped.output and "-j" in helped.output
    assert "--file" not in helped.output
    bare = run(command)
    assert bare.exit_code == 2
    assert "requires an argument" in bare.stderr
    assert bare.stdout == ""


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("UNTAPED_AWX__SERVER__URL", "https://aap.example.com"),
        ("UNTAPED_AWX", '{"server": {"url": "https://aap.example.com"}, "api_prefix": "/api/v2/"}'),
    ],
)
def test_a_variable_sets_the_server(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    del settings["server"]
    monkeypatch.setenv(variable, value)
    with respx.mock(base_url="https://aap.example.com") as mock:
        _mock_me(mock)
        mock.get("/api/v2/ping/").mock(
            return_value=httpx.Response(200, json={"version": "4.5.0", "active_node": "c-1"})
        )
        result = run(["ping", "--format", "raw", "--columns", "version"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "4.5.0"
