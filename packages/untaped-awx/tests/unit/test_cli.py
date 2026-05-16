from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner
from untaped_awx import app
from untaped_core.settings import get_settings


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
        result = CliRunner().invoke(app, ["ping", "--format", "raw", "--columns", "version"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "4.5.0"


def test_ping_requires_base_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    result = CliRunner().invoke(app, ["ping"])
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
        result = CliRunner().invoke(app, [cli_name, "list", "--format", "raw"])

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
        result = CliRunner().invoke(
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

    result = CliRunner().invoke(app, ["job-templates", "list", "--filter", "bogus"])
    assert result.exit_code != 0
    output = result.output + (result.stderr or "")
    assert "KEY=VALUE" in output


def test_apply_help_advertises_parallel() -> None:
    """The top-level ``awx apply`` exposes ``--parallel / -j`` so users
    can speed up directory applies. Surface check only; behaviour is
    covered by the ``ApplyFile`` unit tests."""
    result = CliRunner().invoke(app, ["apply", "--help"])
    assert result.exit_code == 0
    assert "--parallel" in result.output
    assert "-j" in result.output


def test_per_kind_apply_help_advertises_parallel() -> None:
    """Per-resource sub-apps' ``apply`` (e.g. ``awx projects apply``)
    must also expose ``--parallel / -j`` — the per-kind path routes
    through the same ``run_apply`` composition root."""
    result = CliRunner().invoke(app, ["projects", "apply", "--help"])
    assert result.exit_code == 0
    assert "--parallel" in result.output
    assert "-j" in result.output


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
    result = CliRunner().invoke(app, ["apply", "--file", str(yml), "--parallel", "100"])
    assert result.exit_code == 0, result.output
    assert "clamped to 10" in result.output


def test_apply_accepts_file_as_positional(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``awx apply FILE`` — positional — must work; every other "operate
    on a file/name" command in the suite (``workspace import <source>``,
    ``workspace path <name>``, ``jobs get <id>``, …) takes the noun as a
    positional. The ``--file`` option-required shape was the historical
    odd-one-out."""
    cfg = _write_config(tmp_path)
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    yml = tmp_path / "empty.yml"
    yml.write_text("")
    result = CliRunner().invoke(app, ["apply", str(yml)])
    assert result.exit_code == 0, result.output


def test_apply_file_option_still_works_as_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--file`` / ``-f`` is retained for one release as an alias so
    existing scripts and muscle memory keep working. Drop later in a
    separate deprecation PR."""
    cfg = _write_config(tmp_path)
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    yml = tmp_path / "empty.yml"
    yml.write_text("")
    result = CliRunner().invoke(app, ["apply", "--file", str(yml)])
    assert result.exit_code == 0, result.output
    short = CliRunner().invoke(app, ["apply", "-f", str(yml)])
    assert short.exit_code == 0, short.output


def test_apply_option_wins_when_both_positional_and_flag_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a user passes both ``FILE`` positional and ``--file OTHER``,
    the explicit flag wins — matches how a typing-twice user expects an
    explicit flag to override the positional. We verify by passing a
    non-existent positional and a real file via ``--file``; if the
    option didn't win, the apply would fail to read the positional."""
    cfg = _write_config(tmp_path)
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    yml = tmp_path / "empty.yml"
    yml.write_text("")
    bogus = tmp_path / "does-not-exist.yml"
    result = CliRunner().invoke(app, ["apply", str(bogus), "--file", str(yml)])
    assert result.exit_code == 0, result.output


def test_apply_bare_invocation_shows_help() -> None:
    """``no_args_is_help=True`` on ``apply`` survives the positional shift —
    bare ``awx apply`` shows help (exit code 2 is the codebase-wide
    convention for help-on-no-args, same as ``workspace path`` /
    ``workspace add``), not a "Missing option '--file'" error."""
    result = CliRunner().invoke(app, ["apply"])
    assert result.exit_code == 2
    assert "Usage:" in result.output
    assert "Missing option" not in result.output


def test_apply_help_synopsis_shows_file_positional() -> None:
    """The synopsis must advertise ``FILE`` as a positional so users see
    the right shape in ``--help``."""
    result = CliRunner().invoke(app, ["apply", "--help"])
    assert result.exit_code == 0
    assert "FILE" in result.output


def test_per_kind_apply_accepts_file_as_positional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-kind ``awx <kind> apply FILE`` picks up the same shape from
    one edit in ``_add_apply``."""
    cfg = _write_config(tmp_path)
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    yml = tmp_path / "empty.yml"
    yml.write_text("")
    result = CliRunner().invoke(app, ["job-templates", "apply", str(yml)])
    assert result.exit_code == 0, result.output


def test_per_kind_apply_file_option_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--file`` alias on per-kind apply keeps working too."""
    cfg = _write_config(tmp_path)
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    yml = tmp_path / "empty.yml"
    yml.write_text("")
    result = CliRunner().invoke(app, ["job-templates", "apply", "--file", str(yml)])
    assert result.exit_code == 0, result.output


def test_per_kind_apply_bare_invocation_shows_help() -> None:
    """``awx <kind> apply`` with no args shows help (exit code 2 — the
    codebase-wide ``no_args_is_help`` convention), not a "Missing option
    '--file'" error."""
    result = CliRunner().invoke(app, ["job-templates", "apply"])
    assert result.exit_code == 2
    assert "Usage:" in result.output
    assert "Missing option" not in result.output


def test_per_kind_apply_help_shows_file_positional() -> None:
    """Per-kind synopsis must advertise the ``FILE`` positional."""
    result = CliRunner().invoke(app, ["job-templates", "apply", "--help"])
    assert result.exit_code == 0
    assert "FILE" in result.output
