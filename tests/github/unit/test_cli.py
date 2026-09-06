import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

from untaped.capabilities.github.cli import app
from untaped.capabilities.github.settings import GithubSettings
from untaped.settings import get_settings, register_profile_settings
from untaped.testing import CliInvoker


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    # Invoking the github app directly skips the SDK profile-settings
    # registration, so mirror it (idempotent for the same model class)
    # before each test.
    register_profile_settings("github", GithubSettings)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.yml"
    cfg.write_text("profiles:\n  default:\n    github:\n      token: ghp_test\n")
    return cfg


def _write_missing_theme_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "profiles:\n  default:\n    ui:\n      theme: missing\n    github:\n      token: ghp_test\n"
    )
    return cfg


def test_whoami_demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _write_config(tmp_path)
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/user").mock(return_value=httpx.Response(200, json={"login": "octocat", "id": 1}))
        result = CliInvoker().invoke(app, ["whoami", "--format", "raw", "--columns", "login"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "octocat"


def test_whoami_pipe_tags_github_user_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/user").mock(return_value=httpx.Response(200, json={"login": "octocat", "id": 1}))
        result = CliInvoker().invoke(app, ["whoami", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.splitlines()[0])
    assert envelope["kind"] == "github.user"
    assert envelope["record"]["login"] == "octocat"


def test_whoami_json_emits_bare_object(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A single entity emits a bare JSON object ``{…}``, not a one-element
    array ``[{…}]`` (the ``emit`` single-record contract)."""
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/user").mock(return_value=httpx.Response(200, json={"login": "octocat", "id": 1}))
        result = CliInvoker().invoke(app, ["whoami", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    assert payload["login"] == "octocat"


def test_whoami_table_renders_detail_view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A single entity renders as a vertical key:value detail view under the
    default config — not a boxed one-row table (the ``emit`` single-record
    contract). This is the behavioural change from ``render_rows([x])``."""
    cfg = _write_config(tmp_path)
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/user").mock(return_value=httpx.Response(200, json={"login": "octocat", "id": 1}))
        result = CliInvoker().invoke(app, ["whoami", "--format", "table"])

    assert result.exit_code == 0, result.output
    assert "login: octocat" in result.stdout
    assert "id: 1" in result.stdout
    # Detail view is vertical key:value lines, not a bordered table grid.
    assert "─" not in result.stdout
    assert "│" not in result.stdout


def test_whoami_raw_ignores_invalid_ui_theme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _write_missing_theme_config(tmp_path)
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/user").mock(return_value=httpx.Response(200, json={"login": "octocat", "id": 1}))
        result = CliInvoker().invoke(app, ["whoami", "--format", "raw"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "octocat"
    assert "\x1b[" not in result.output
    assert "unknown UI theme" not in result.output


def test_whoami_rejects_command_local_profile_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Profile selection moved to the root `untaped --profile` option
    # (plugin API v4, accepted in any token position). The tool's own
    # commands no longer define a local --profile, so it must be rejected
    # as an unknown option (usage error, exit 2).
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    result = CliInvoker().invoke(
        app, ["whoami", "--profile", "stage", "--format", "raw", "--columns", "login"]
    )

    assert result.exit_code == 2, result.output
    assert "--profile" in result.output


def test_whoami_requires_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    result = CliInvoker().invoke(app, ["whoami"])
    assert result.exit_code != 0
    assert "token" in str(result.exception) or "token" in result.output


def test_whoami_rejects_blank_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text('profiles:\n  default:\n    github:\n      token: "   "\n')
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    result = CliInvoker().invoke(app, ["whoami"])
    assert result.exit_code != 0
    assert "token" in str(result.exception) or "token" in result.output
