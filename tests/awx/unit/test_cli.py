import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

from untaped.capabilities.awx.cli import app
from untaped.settings import get_settings
from untaped.testing import CliInvoker


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _write_config(tmp_path: Path, *, api_prefix: str | None = None) -> Path:
    cfg = tmp_path / "config.yml"
    body = """
        profiles:
          default:
            awx:
              base_url: https://aap.example.com
              token: secret
        """
    if api_prefix is not None:
        body += f"      api_prefix: {api_prefix}\n"
    cfg.write_text(body)
    return cfg


@pytest.mark.parametrize(
    ("api_prefix", "expected_path"),
    [
        (None, "/api/controller/v2/ping/"),  # AAP default
        ("/api/v2/", "/api/v2/ping/"),  # upstream AWX
    ],
)
def test_ping_uses_configured_api_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    api_prefix: str | None,
    expected_path: str,
) -> None:
    cfg = _write_config(tmp_path, api_prefix=api_prefix)
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get(expected_path).mock(
            return_value=httpx.Response(
                200,
                json={"version": "4.5.0", "active_node": "controller-1"},
            )
        )
        result = CliInvoker().invoke(app, ["ping", "--format", "raw", "--columns", "version"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "4.5.0"


def test_ping_table_renders_detail_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single entity renders as a vertical key:value detail view under the
    default config — not a boxed one-row table (the ``emit`` single-record
    contract). Before the migration this default-config render was a grid."""
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          default:
            awx:
              base_url: https://aap.example.com
              token: secret
              api_prefix: /api/v2/
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()

    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/ping/").mock(
            return_value=httpx.Response(
                200,
                json={"version": "4.5.0", "active_node": "controller-1"},
            )
        )
        result = CliInvoker().invoke(app, ["ping", "--format", "table"])

    assert result.exit_code == 0, result.output
    assert "version: 4.5.0" in result.stdout
    assert "active_node: controller-1" in result.stdout
    assert not any(ch in result.stdout for ch in "╭╮╰╯┌┐└┘│─")


def test_ping_json_emits_bare_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ping --format json`` emits a bare object ``{…}`` (not ``[{…}]``)."""
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          default:
            awx:
              base_url: https://aap.example.com
              token: secret
              api_prefix: /api/v2/
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()

    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/ping/").mock(
            return_value=httpx.Response(200, json={"version": "4.5.0", "active_node": "c-1"})
        )
        result = CliInvoker().invoke(app, ["ping", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    assert payload["version"] == "4.5.0"


def test_ping_rejects_command_local_profile_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Profile selection is built into the SDK.

    Commands do not expose a local ``--profile``, so passing one
    directly to the tool app is an unknown-option error (exit 2).
    """
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          default:
            awx:
              base_url: https://aap.example.com
              token: default-token
              api_prefix: /api/v2/
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

    result = CliInvoker().invoke(
        app,
        ["ping", "--profile", "stage", "--format", "raw", "--columns", "version"],
    )

    assert result.exit_code == 2, result.output


def test_ping_requires_base_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    result = CliInvoker().invoke(app, ["ping"])
    assert result.exit_code != 0
    assert "base_url" in str(result.exception) or "base_url" in result.output


@pytest.mark.parametrize("cli_name", ["organizations", "credential-types", "job-templates"])
def test_list_does_not_auto_apply_default_organization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cli_name: str,
) -> None:
    """``awx.default_organization`` is for name disambiguation on
    ``get`` / ``launch`` / ``update`` only — ``list`` filters are now
    explicit via ``--filter``. Auto-applying the default would (a) break
    global kinds (Organization, CredentialType have no organization
    column), and (b) silently scope a list the user expected to be
    cluster-wide.
    """
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          default:
            awx:
              base_url: https://aap.example.com
              token: secret
              api_prefix: /api/v2/
              default_organization: Default
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

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
        result = CliInvoker().invoke(app, [cli_name, "list", "--format", "raw"])

    assert result.exit_code == 0, result.output
    assert captured, "no request captured"
    for req in captured:
        assert "organization__name" not in req.url.params, (
            f"{cli_name!r} list auto-applied default_organization: {req.url.params}"
        )


def test_list_filter_passes_through_to_awx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--filter KEY=VALUE`` must reach AWX as a verbatim URL param so any
    Django-style lookup (``__name``, ``__icontains``, ``__contains``,
    exact match, …) works without code changes."""
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          default:
            awx:
              base_url: https://aap.example.com
              token: secret
              api_prefix: /api/v2/
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

    captured: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"count": 0, "next": None, "previous": None, "results": []})

    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/job_templates/").mock(side_effect=_record)
        result = CliInvoker().invoke(
            app,
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


def test_list_filter_rejects_malformed_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed ``--filter`` (no ``=``) must fail up front — silently
    posting it to AWX surfaces as an opaque HTTP 400."""
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          default:
            awx:
              base_url: https://aap.example.com
              token: secret
              api_prefix: /api/v2/
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

    result = CliInvoker().invoke(app, ["job-templates", "list", "--filter", "bogus"])
    assert result.exit_code != 0
    output = result.output + (result.stderr or "")
    assert "KEY=VALUE" in output


def test_apply_help_advertises_parallel() -> None:
    """The top-level ``awx apply`` exposes ``--parallel / -j`` so users
    can speed up directory applies. Surface check only; behaviour is
    covered by the ``ApplyFile`` unit tests."""
    result = CliInvoker().invoke(app, ["apply", "--help"])
    assert result.exit_code == 0
    assert "--parallel" in result.output
    assert "-j" in result.output


def test_per_kind_apply_help_advertises_parallel() -> None:
    """Per-resource sub-apps' ``apply`` (e.g. ``awx projects apply``)
    must also expose ``--parallel / -j`` — the per-kind path routes
    through the same ``run_apply`` composition root."""
    result = CliInvoker().invoke(app, ["projects", "apply", "--help"])
    assert result.exit_code == 0
    assert "--parallel" in result.output
    assert "-j" in result.output


def test_get_bare_invocation_is_usage_error_without_opening_context() -> None:
    result = CliInvoker().invoke(app, ["job-templates", "get"])

    assert result.exit_code == 2
    assert "error: provide JobTemplate name(s) or --stdin" in result.stderr
    assert "awx.base_url" not in result.output


def test_per_kind_apply_rejects_parallel_below_one_before_opening_context(tmp_path: Path) -> None:
    yml = tmp_path / "empty.yml"
    yml.write_text("")

    result = CliInvoker().invoke(app, ["job-templates", "apply", str(yml), "--parallel", "0"])

    assert result.exit_code != 0
    assert "parallel" in result.output
    assert "awx.base_url" not in result.output


def test_apply_emits_clamp_warning_above_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``awx apply --parallel`` above the cap stays accepted (clamped)
    but a stderr warning surfaces the truncation so users notice when
    they ask for more concurrency than they get. Without this test the
    warning could silently regress to a no-op."""
    cfg = _write_config(tmp_path)
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    yml = tmp_path / "empty.yml"
    yml.write_text("")  # zero docs → no AWX calls, runner just prints rows
    result = CliInvoker().invoke(app, ["apply", str(yml), "--parallel", "100"])
    assert result.exit_code == 0, result.output
    assert "clamped to 10" in result.output
    assert "httpx.Limits.max_connections=10" in result.output


@pytest.mark.parametrize(
    "template",
    [
        pytest.param(["apply", "FILE"], id="top-level-positional"),
        pytest.param(["job-templates", "apply", "FILE"], id="per-kind-positional"),
    ],
)
def test_apply_accepts_positional_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    template: list[str],
) -> None:
    """Apply commands take the YAML file as a required positional argument."""
    cfg = _write_config(tmp_path)
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    yml = tmp_path / "empty.yml"
    yml.write_text("")
    args = [str(yml) if a == "FILE" else a for a in template]
    result = CliInvoker().invoke(app, args)
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    "args_template",
    [
        pytest.param(["apply", "--file", "FILE"], id="top-level-long"),
        pytest.param(["apply", "-f", "FILE"], id="top-level-short"),
        pytest.param(["job-templates", "apply", "--file", "FILE"], id="per-kind-long"),
        pytest.param(["job-templates", "apply", "-f", "FILE"], id="per-kind-short"),
    ],
)
def test_apply_rejects_removed_file_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    args_template: list[str],
) -> None:
    cfg = _write_config(tmp_path)
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    yml = tmp_path / "empty.yml"
    yml.write_text("")
    args = [str(yml) if a == "FILE" else a for a in args_template]
    result = CliInvoker().invoke(app, args)
    assert result.exit_code != 0
    assert "--file" in result.output or "-f" in result.output


def test_top_level_apply_bare_invocation_is_missing_argument_error() -> None:
    """Top-level multi-kind ``apply`` still requires a positional file: exit 2,
    Cyclopts' ``requires an argument`` message on stderr, nothing on stdout —
    the suite convention for required positionals."""
    result = CliInvoker().invoke(app, ["apply"])
    assert result.exit_code == 2
    assert "requires an argument" in result.stderr
    assert result.stdout == ""


def test_per_kind_apply_bare_invocation_needs_file_or_stdin() -> None:
    """Per-kind ``apply`` takes a file *or* ``--stdin`` (mass-patch a selection).
    With neither it's a usage error that names both options; exit 2, clean
    stdout."""
    result = CliInvoker().invoke(app, ["job-templates", "apply"])
    assert result.exit_code == 2
    assert "--stdin" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("cmd", [["apply", "--help"], ["job-templates", "apply", "--help"]])
def test_apply_help_synopsis_shows_file_positional(cmd: list[str]) -> None:
    """The help must describe positional file input without reviving ``--file``."""
    result = CliInvoker().invoke(app, cmd)
    assert result.exit_code == 0
    assert "YAML file" in result.output
    assert "--file" not in result.output
