"""End-to-end CLI tests for ``untaped github repos``."""

from __future__ import annotations

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
    register_profile_settings("github", GithubSettings)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.yml"
    cfg.write_text("profiles:\n  default:\n    github:\n      token: ghp_test\n")
    return cfg


def _repo(full_name: str, *, archived: bool = False, fork: bool = False) -> dict[str, object]:
    name = full_name.rsplit("/", 1)[1]
    return {
        "full_name": full_name,
        "name": name,
        "html_url": f"https://github.com/{full_name}",
        "clone_url": f"https://github.com/{full_name}.git",
        "ssh_url": f"git@github.com:{full_name}.git",
        "default_branch": "main",
        "private": True,
        "archived": archived,
        "fork": fork,
    }


def test_repos_list_combines_scopes_filters_and_outputs_raw_clone_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    _repo("acme/zeta"),
                    _repo("acme/play-api"),
                    _repo("acme/play-old", archived=True),
                    _repo("acme/play-fork", fork=True),
                ],
            )
        )
        mock.get("/orgs/platform/teams/ops/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    _repo("platform/play-role"),
                    _repo("acme/play-api"),
                ],
            )
        )
        result = CliInvoker().invoke(
            app,
            [
                "repos",
                "list",
                "play*",
                "--org",
                "acme",
                "--team",
                "platform/ops",
                "--no-archived",
                "--no-fork",
                "--format",
                "raw",
                "--columns",
                "ssh_url",
            ],
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "git@github.com:acme/play-api.git",
        "git@github.com:platform/play-role.git",
    ]
    assert "Listing repositories" in result.stderr
    assert "Listing repositories" not in result.stdout


def test_repos_list_accepts_org_qualified_team_without_org_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/teams/backend/repos").mock(
            return_value=httpx.Response(200, json=[_repo("acme/play-api")])
        )
        result = CliInvoker().invoke(
            app,
            [
                "repos",
                "list",
                "play*",
                "--team",
                "acme/backend",
                "--format",
                "raw",
                "--columns",
                "ssh_url",
            ],
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["git@github.com:acme/play-api.git"]


def test_repos_list_expands_bare_team_with_one_org_as_additive_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/repos").mock(
            return_value=httpx.Response(200, json=[_repo("acme/play-org")])
        )
        mock.get("/orgs/acme/teams/backend/repos").mock(
            return_value=httpx.Response(200, json=[_repo("acme/play-api")])
        )
        result = CliInvoker().invoke(
            app,
            [
                "repos",
                "list",
                "play*",
                "--org",
                "acme",
                "--team",
                "backend",
                "--format",
                "raw",
                "--columns",
                "full_name",
            ],
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["acme/play-api", "acme/play-org"]


def test_repos_list_pipe_tags_github_repo_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/repos").mock(
            return_value=httpx.Response(200, json=[_repo("acme/play-api")])
        )
        result = CliInvoker().invoke(
            app,
            ["repos", "list", "--org", "acme", "--format", "pipe"],
        )

    assert result.exit_code == 0, result.output
    [line] = result.stdout.splitlines()
    envelope = json.loads(line)
    assert envelope["untaped"] == "1"
    assert envelope["kind"] == "github.repo"
    assert envelope["record"]["full_name"] == "acme/play-api"
    assert envelope["record"]["ssh_url"] == "git@github.com:acme/play-api.git"


def test_repos_list_requires_org_or_team_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    result = CliInvoker().invoke(app, ["repos", "list", "play*"])

    assert result.exit_code != 0
    assert "requires --org or --team" in result.output
    assert "user-owned" in result.output


def test_repos_list_rejects_regex_without_pattern(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    result = CliInvoker().invoke(app, ["repos", "list", "--org", "acme", "--regex"])

    assert result.exit_code != 0
    assert "--regex requires PATTERN" in result.output


def test_repos_list_rejects_malformed_team_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    result = CliInvoker().invoke(app, ["repos", "list", "--team", "backend"])

    assert result.exit_code != 0
    assert "ORG/SLUG" in result.output


def test_repos_list_rejects_bare_team_with_multiple_orgs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    result = CliInvoker().invoke(
        app,
        ["repos", "list", "--org", "acme", "--org", "platform", "--team", "backend"],
    )

    assert result.exit_code != 0
    assert "exactly one --org" in result.output


def test_repos_list_rejects_invalid_regex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    result = CliInvoker().invoke(app, ["repos", "list", "[", "--org", "acme", "--regex"])

    assert result.exit_code != 0
    assert "invalid regular expression" in result.output


def test_repos_list_help_documents_pattern_targeting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    result = CliInvoker().invoke(app, ["repos", "list", "--help"])

    assert result.exit_code == 0, result.output
    assert "glob" in result.output
    assert "full_name" in result.output
    assert "unanchored" in result.output
    assert "additive" in result.output
    assert "exactly one --org" in result.output
    assert "--regex" in result.output
