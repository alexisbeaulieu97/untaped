from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
import typer
from typer.testing import CliRunner
from untaped_awx import app
from untaped_awx.cli._apply_runner import resolve_apply_file
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
    result = CliRunner().invoke(app, ["apply", str(yml), "--parallel", "100"])
    assert result.exit_code == 0, result.output
    assert "clamped to 10" in result.output
    assert "httpx.Limits.max_connections=10" in result.output


def test_resolve_apply_file_rejects_neither_set() -> None:
    """Body-level guard for ``apply --yes`` (no file) — Typer can't trip
    ``no_args_is_help`` once any flag is on the line."""
    with pytest.raises(typer.BadParameter, match="FILE is required"):
        resolve_apply_file(None, None)


def test_resolve_apply_file_option_wins_over_positional(tmp_path: Path) -> None:
    """``--file`` wins when both are given so an explicit flag overrides
    a leftover positional."""
    positional = tmp_path / "a.yml"
    option = tmp_path / "b.yml"
    assert resolve_apply_file(positional, option) == option
    assert resolve_apply_file(positional, None) == positional
    assert resolve_apply_file(None, option) == option


@pytest.mark.parametrize(
    "template",
    [
        pytest.param(["apply", "FILE"], id="positional"),
        pytest.param(["apply", "--file", "FILE"], id="file-long"),
        pytest.param(["apply", "-f", "FILE"], id="file-short"),
        # "Option wins" — a real ``--file`` paired with a non-existent
        # positional. If the positional ever won, ``read_resources``
        # would fail and the test would non-zero.
        pytest.param(["apply", "BOGUS", "--file", "FILE"], id="option-wins"),
        pytest.param(["job-templates", "apply", "FILE"], id="per-kind-positional"),
        pytest.param(["job-templates", "apply", "--file", "FILE"], id="per-kind-file-long"),
        pytest.param(["job-templates", "apply", "-f", "FILE"], id="per-kind-file-short"),
    ],
)
def test_apply_accepts_positional_and_file_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    template: list[str],
) -> None:
    """The file may be passed as a positional or via the ``--file`` / ``-f``
    backward-compat alias; when both are given, ``--file`` wins."""
    cfg = _write_config(tmp_path)
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    yml = tmp_path / "empty.yml"
    yml.write_text("")
    bogus = tmp_path / "does-not-exist.yml"
    args = [str(yml) if a == "FILE" else str(bogus) if a == "BOGUS" else a for a in template]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    ("args_template", "expect_warning"),
    [
        pytest.param(["apply", "FILE"], False, id="positional-no-warning"),
        pytest.param(["apply", "--file", "FILE"], True, id="long-flag-warns"),
        pytest.param(["apply", "-f", "FILE"], True, id="short-flag-warns"),
        pytest.param(
            ["job-templates", "apply", "FILE"], False, id="per-kind-positional-no-warning"
        ),
        pytest.param(["job-templates", "apply", "--file", "FILE"], True, id="per-kind-long-warns"),
    ],
)
def test_apply_alias_emits_deprecation_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    args_template: list[str],
    expect_warning: bool,
) -> None:
    """``--file`` / ``-f`` is deprecated for one release; using it must
    emit a stderr warning so users migrate before the alias is dropped.
    The positional form must NOT warn."""
    cfg = _write_config(tmp_path)
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    yml = tmp_path / "empty.yml"
    yml.write_text("")
    args = [str(yml) if a == "FILE" else a for a in args_template]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert ("--file/-f is deprecated" in result.output) is expect_warning


@pytest.mark.parametrize("cmd", [["apply"], ["job-templates", "apply"]])
def test_apply_bare_invocation_shows_help(cmd: list[str]) -> None:
    """Bare ``apply`` shows help via ``no_args_is_help`` (exit 2 — same
    convention as ``workspace path`` / ``workspace add``), not a
    "Missing option '--file'" error left over from the old shape."""
    result = CliRunner().invoke(app, cmd)
    assert result.exit_code == 2
    assert "Usage:" in result.output
    assert "Missing option" not in result.output


@pytest.mark.parametrize("cmd", [["apply", "--help"], ["job-templates", "apply", "--help"]])
def test_apply_help_synopsis_shows_file_positional(cmd: list[str]) -> None:
    """The synopsis must advertise ``FILE`` as a positional."""
    result = CliRunner().invoke(app, cmd)
    assert result.exit_code == 0
    assert "FILE" in result.output
