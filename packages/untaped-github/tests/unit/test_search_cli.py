"""End-to-end CLI tests for ``untaped github search`` (HTTP mocked via respx)."""

from __future__ import annotations

import json
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


def test_search_repos_injects_at_me_and_renders_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    payload = {
        "total_count": 1,
        "incomplete_results": False,
        "items": [
            {
                "id": 1,
                "name": "alpha",
                "full_name": "octocat/alpha",
                "html_url": "https://github.com/octocat/alpha",
                "stargazers_count": 7,
            }
        ],
    }
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/search/repositories").mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = CliRunner().invoke(
            app,
            [
                "search",
                "repos",
                "--language",
                "python",
                "--format",
                "raw",
                "--columns",
                "full_name",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "octocat/alpha" in result.stdout
    sent_q = route.calls[0].request.url.params["q"]
    assert "user:@me" in sent_q
    assert "language:python" in sent_q


def test_search_repos_with_explicit_org_does_not_inject_at_me(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/search/repositories").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        result = CliRunner().invoke(app, ["search", "repos", "--org", "acme", "--format", "json"])

    assert result.exit_code == 0, result.output
    sent_q = route.calls[0].request.url.params["q"]
    assert "org:acme" in sent_q
    assert "user:@me" not in sent_q


def test_search_repos_team_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    team_repos = [
        {"full_name": "acme/api"},
        {"full_name": "acme/web"},
    ]
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/teams/backend/repos").mock(
            return_value=httpx.Response(200, json=team_repos)
        )
        search_route = mock.get("/search/repositories").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        result = CliRunner().invoke(
            app,
            ["search", "repos", "--org", "acme", "--team", "backend", "--format", "json"],
        )

    assert result.exit_code == 0, result.output
    sent_q = search_route.calls[0].request.url.params["q"]
    assert "repo:acme/api" in sent_q
    assert "repo:acme/web" in sent_q


def test_search_code_passes_query_and_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    payload = {
        "items": [
            {
                "name": "main.py",
                "path": "src/main.py",
                "sha": "abc",
                "html_url": "https://x",
                "repository": {"full_name": "me/proj"},
            }
        ]
    }
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/search/code").mock(return_value=httpx.Response(200, json=payload))
        result = CliRunner().invoke(
            app,
            ["search", "code", "TODO", "--language", "python", "--format", "json"],
        )

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert parsed[0]["repo"] == "me/proj"
    sent_q = route.calls[0].request.url.params["q"]
    assert "TODO" in sent_q
    assert "language:python" in sent_q
    assert "user:@me" in sent_q


def test_search_issues_filters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    payload = {
        "items": [
            {
                "id": 1,
                "number": 1,
                "title": "first",
                "state": "open",
                "html_url": "https://x",
                "repository_url": "https://api.github.com/repos/me/p",
                "user": {"login": "octocat"},
            }
        ]
    }
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/search/issues").mock(return_value=httpx.Response(200, json=payload))
        result = CliRunner().invoke(
            app,
            [
                "search",
                "issues",
                "--state",
                "open",
                "--kind",
                "pr",
                "--label",
                "bug",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 0, result.output
    sent_q = route.calls[0].request.url.params["q"]
    for token in ("is:pr", "is:open", "label:bug", "user:@me"):
        assert token in sent_q


def test_search_users_no_at_me(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    payload = {
        "items": [
            {
                "id": 1,
                "login": "octocat",
                "type": "User",
                "html_url": "https://github.com/octocat",
            }
        ]
    }
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/search/users").mock(return_value=httpx.Response(200, json=payload))
        result = CliRunner().invoke(
            app,
            ["search", "users", "--kind", "org", "--location", "montreal", "--format", "json"],
        )

    assert result.exit_code == 0, result.output
    sent_q = route.calls[0].request.url.params["q"]
    assert "user:@me" not in sent_q
    assert "type:org" in sent_q
    assert "location:montreal" in sent_q


def test_search_repos_follows_link_header_for_pagination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    page1 = {
        "items": [
            {"id": 1, "name": "a", "full_name": "me/a", "html_url": "https://x"},
        ]
    }
    page2 = {
        "items": [
            {"id": 2, "name": "b", "full_name": "me/b", "html_url": "https://x"},
        ]
    }
    link = '<https://api.github.com/search/repositories?page=2>; rel="next"'

    with respx.mock(base_url="https://api.github.com") as mock:
        # First page returns Link: next; second page returns no Link.
        mock.get("/search/repositories", params={"page": "2"}).mock(
            return_value=httpx.Response(200, json=page2)
        )
        mock.get("/search/repositories").mock(
            return_value=httpx.Response(200, json=page1, headers={"Link": link})
        )
        result = CliRunner().invoke(app, ["search", "repos", "--format", "json"])

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert [r["full_name"] for r in parsed] == ["me/a", "me/b"]


def test_search_repos_respects_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    items = [
        {"id": i, "name": f"r{i}", "full_name": f"me/r{i}", "html_url": "https://x"}
        for i in range(5)
    ]
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/search/repositories").mock(
            return_value=httpx.Response(200, json={"items": items})
        )
        result = CliRunner().invoke(app, ["search", "repos", "--limit", "2", "--format", "json"])

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert len(parsed) == 2


def test_search_repos_default_limit_is_30(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Bare `search repos` (no --limit) used to paginate until GitHub's
    # 1000-result cap. The default is now 30 so a casual exploratory
    # query costs one of the user's 30/min search-rate-limit budget.
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    items = [
        {"id": i, "name": f"r{i}", "full_name": f"me/r{i}", "html_url": "https://x"}
        for i in range(50)
    ]
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/search/repositories").mock(
            return_value=httpx.Response(200, json={"items": items})
        )
        result = CliRunner().invoke(app, ["search", "repos", "--format", "json"])

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert len(parsed) == 30
    assert route.calls[0].request.url.params["per_page"] == "30"
    assert route.call_count == 1


def test_search_repos_limit_1000_paginates_fully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `--limit 1000` is the documented escape hatch for opting into
    # GitHub's hard maximum; the paginator must still walk the Link
    # header chain when the user asks for it explicitly.
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    page1 = {
        "items": [
            {"id": i, "name": f"r{i}", "full_name": f"me/r{i}", "html_url": "https://x"}
            for i in range(100)
        ]
    }
    page2 = {
        "items": [
            {"id": i, "name": f"r{i}", "full_name": f"me/r{i}", "html_url": "https://x"}
            for i in range(100, 200)
        ]
    }
    link = '<https://api.github.com/search/repositories?page=2>; rel="next"'
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/search/repositories", params={"page": "2"}).mock(
            return_value=httpx.Response(200, json=page2)
        )
        mock.get("/search/repositories").mock(
            return_value=httpx.Response(200, json=page1, headers={"Link": link})
        )
        result = CliRunner().invoke(app, ["search", "repos", "--limit", "1000", "--format", "json"])

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert len(parsed) == 200


def test_search_repos_limit_zero_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A zero limit is a usage error, not a synonym for "all results".
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    result = CliRunner().invoke(app, ["search", "repos", "--limit", "0"])
    assert result.exit_code != 0


def test_search_repos_help_advertises_default_30(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    result = CliRunner().invoke(app, ["search", "repos", "--help"])
    assert result.exit_code == 0, result.output
    # Typer renders the default inline next to the option.
    assert "30" in result.output
    assert "1000" in result.output  # cap mentioned in the help string


def test_search_issues_default_limit_is_30(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Lock the contract on a second subcommand so the default isn't
    # accidentally regressed on only one of the four call sites.
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    items = [
        {
            "id": i,
            "number": i,
            "title": f"t{i}",
            "state": "open",
            "html_url": "https://x",
            "repository_url": "https://api.github.com/repos/me/p",
            "user": {"login": "octocat"},
        }
        for i in range(50)
    ]
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/search/issues").mock(
            return_value=httpx.Response(200, json={"items": items})
        )
        result = CliRunner().invoke(app, ["search", "issues", "--format", "json"])

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert len(parsed) == 30
    assert route.calls[0].request.url.params["per_page"] == "30"


def test_search_team_without_org_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    result = CliRunner().invoke(app, ["search", "repos", "--team", "backend"])
    assert result.exit_code != 0
    assert "team" in result.output.lower() or "team" in str(result.exception).lower()


def test_search_team_with_multiple_orgs_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Multiple --org with --team used to silently pick the first; must
    # now fail loudly so the user knows which org scoped the lookup.
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    result = CliRunner().invoke(
        app,
        ["search", "repos", "--org", "a", "--org", "b", "--team", "backend"],
    )
    assert result.exit_code != 0
    assert "single --org" in result.output
