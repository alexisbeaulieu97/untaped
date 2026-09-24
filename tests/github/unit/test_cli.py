"""End-to-end CLI tests for ``untaped github whoami`` and token errors."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from untaped.capabilities.github.cli import app
from untaped.testing import CliInvoker, CliResult


def _whoami(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    *,
    github: str = "token: ghp_test",
    ui: str = "",
    response: httpx.Response | None = None,
) -> CliResult:
    cfg = tmp_path / "config.yml"
    ui_section = f"    ui:\n      {ui}\n" if ui else ""
    cfg.write_text(f"profiles:\n  default:\n{ui_section}    github:\n      {github}\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    reply = response or httpx.Response(200, json={"login": "octocat", "id": 1, "extra": "x"})
    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        mock.get("/user").mock(return_value=reply)
        return CliInvoker().invoke(app, ["whoami", *args])


def test_whoami_renders_one_user_record_per_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _whoami(tmp_path, monkeypatch, ["--format", "raw"])
    as_json = _whoami(tmp_path, monkeypatch, ["--format", "json"])
    pipe = _whoami(tmp_path, monkeypatch, ["--format", "pipe"])
    table = _whoami(tmp_path, monkeypatch, ["--format", "table"])

    assert raw.stdout.strip() == "octocat"
    # A single entity is a bare JSON object and a vertical detail view.
    assert json.loads(as_json.stdout) == {"login": "octocat", "id": 1, "name": None, "email": None}
    envelope = json.loads(pipe.stdout.splitlines()[0])
    assert (envelope["kind"], envelope["record"]["login"]) == ("github.user", "octocat")
    assert "login: octocat" in table.stdout
    assert "│" not in table.stdout


def test_whoami_raw_ignores_invalid_ui_theme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _whoami(tmp_path, monkeypatch, ["--format", "raw"], ui="theme: missing")

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "octocat"
    assert "unknown UI theme" not in result.output


@pytest.mark.parametrize("github", ["base_url: https://api.github.com", 'token: "   "'])
def test_whoami_without_a_token_fails_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, github: str
) -> None:
    result = _whoami(tmp_path, monkeypatch, [], github=github)

    assert result.exit_code == 1
    assert "token" in result.stderr


def test_whoami_rejected_token_hints_at_setting_a_new_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _whoami(
        tmp_path, monkeypatch, [], response=httpx.Response(401, json={"message": "Bad"})
    )

    assert result.exit_code == 1
    assert "error: GitHub rejected the configured token (HTTP 401)" in result.stderr
    assert "hint: run `untaped config set github.token --prompt`" in result.stderr
