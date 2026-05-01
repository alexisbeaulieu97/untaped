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
        awx:
          base_url: https://aap.example.com
          token: secret
        """
    if api_prefix is not None:
        body += f"  api_prefix: {api_prefix}\n"
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


@pytest.mark.parametrize("cli_name", ["organizations", "credential-types"])
def test_default_organization_not_applied_to_global_specs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cli_name: str,
) -> None:
    """``awx.default_organization`` must not leak into queries for kinds
    whose ``identity_keys`` don't include ``organization`` — those records
    have no ``organization_name`` column and the filter would silently
    return zero results.
    """
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
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

    api_path = "credential_types" if cli_name == "credential-types" else cli_name
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get(f"/api/v2/{api_path}/").mock(side_effect=_record)
        result = CliRunner().invoke(app, [cli_name, "list", "--format", "raw"])

    assert result.exit_code == 0, result.output
    assert captured, "no request captured"
    for req in captured:
        assert "organization__name" not in req.url.params, (
            f"global spec {cli_name!r} got an org filter: {req.url.params}"
        )
