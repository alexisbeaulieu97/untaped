from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner
from untaped_core.settings import get_settings
from untaped_github import app


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.yml"
    cfg.write_text("profiles:\n  default:\n    github:\n      token: ghp_test\n")
    return cfg


def test_whoami_demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _write_config(tmp_path)
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/user").mock(return_value=httpx.Response(200, json={"login": "octocat", "id": 1}))
        result = CliRunner().invoke(app, ["whoami", "--format", "raw", "--columns", "login"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "octocat"


def test_whoami_requires_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    result = CliRunner().invoke(app, ["whoami"])
    assert result.exit_code != 0
    assert "token" in str(result.exception) or "token" in result.output
