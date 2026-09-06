"""End-to-end CLI tests for ``untaped github search`` (HTTP mocked via respx)."""

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


def _write_list_view_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "profiles:\n"
        "  default:\n"
        "    ui:\n"
        "      collection_view: list\n"
        "    github:\n"
        "      token: ghp_test\n"
    )
    return cfg


def _write_missing_theme_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "profiles:\n  default:\n    ui:\n      theme: missing\n    github:\n      token: ghp_test\n"
    )
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
        result = CliInvoker().invoke(
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


def test_search_repos_table_honors_list_collection_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_list_view_config(tmp_path)))

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
        mock.get("/search/repositories").mock(return_value=httpx.Response(200, json=payload))
        result = CliInvoker().invoke(app, ["search", "repos", "--format", "table"])

    assert result.exit_code == 0, result.output
    assert "full_name: octocat/alpha" in result.stdout
    assert "name: alpha" in result.stdout
    assert "─" not in result.stdout
    assert "│" not in result.stdout


def test_search_repos_raw_ignores_invalid_ui_theme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_missing_theme_config(tmp_path)))

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
        mock.get("/search/repositories").mock(return_value=httpx.Response(200, json=payload))
        result = CliInvoker().invoke(app, ["search", "repos", "--format", "raw"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "octocat/alpha"
    assert "\x1b[" not in result.output
    assert "unknown UI theme" not in result.output


def test_search_repos_rejects_command_local_profile_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Profile selection moved to the root `untaped --profile` option
    # (plugin API v4, accepted in any token position). The tool's own
    # commands no longer define a local --profile, so it must be rejected
    # as an unknown option (usage error, exit 2).
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    result = CliInvoker().invoke(
        app,
        ["search", "repos", "--profile", "stage", "--format", "json"],
    )

    assert result.exit_code == 2, result.output
    assert "--profile" in result.output


def test_search_repos_with_explicit_org_does_not_inject_at_me(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/search/repositories").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        result = CliInvoker().invoke(app, ["search", "repos", "--org", "acme", "--format", "json"])

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
        result = CliInvoker().invoke(
            app,
            ["search", "repos", "--team", "acme/backend", "--format", "json"],
        )

    assert result.exit_code == 0, result.output
    sent_q = search_route.calls[0].request.url.params["q"]
    assert sent_q == "(repo:acme/api OR repo:acme/web)"


def test_search_repos_batches_team_scopes_by_search_constraints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    team_repos = [
        {"full_name": (f"Desjardins/infrasinteroutils-gha-actions-commun-intergiciel-repo{i}")}
        for i in range(8)
    ]
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/Desjardins/teams/backend/repos").mock(
            return_value=httpx.Response(200, json=team_repos)
        )
        search_route = mock.get("/search/repositories").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        result = CliInvoker().invoke(
            app,
            [
                "search",
                "repos",
                "uses: "
                "Desjardins/infrasinteroutils-gha-actions-commun-intergiciel"
                "/.github/actions/set-constants-url",
                "--team",
                "Desjardins/backend",
                "--archived",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 0, result.output
    queries = [call.request.url.params["q"] for call in search_route.calls]
    assert len(queries) > 1
    assert sum(q.count("repo:") for q in queries) == len(team_repos)
    assert all(q.count("repo:") <= 6 for q in queries)
    assert all(q.count(" OR ") <= 5 for q in queries)
    assert all("archived:true" in q for q in queries)


def test_search_repos_batches_short_team_scopes_by_operator_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    team_repos = [{"full_name": f"acme/r{i}"} for i in range(13)]
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/teams/backend/repos").mock(
            return_value=httpx.Response(200, json=team_repos)
        )
        search_route = mock.get("/search/repositories").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        result = CliInvoker().invoke(
            app,
            [
                "search",
                "repos",
                "uses: acme/action",
                "--team",
                "acme/backend",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 0, result.output
    queries = [call.request.url.params["q"] for call in search_route.calls]
    assert len(queries) == 3
    assert sum(q.count("repo:") for q in queries) == len(team_repos)
    assert all(q.count("repo:") <= 6 for q in queries)
    assert all(q.count(" OR ") <= 5 for q in queries)


def test_search_repos_oversized_raw_query_fails_before_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        search_route = mock.get("/search/repositories").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        result = CliInvoker().invoke(
            app,
            [
                "search",
                "repos",
                "x" * 257,
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 1
    assert search_route.call_count == 0
    assert "query text length" in result.stderr
    assert "256" in result.stderr
    assert "narrow" in result.stderr


def test_search_repos_422_reports_query_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/search/repositories").mock(
            return_value=httpx.Response(
                422,
                json={
                    "message": "Validation Failed",
                    "errors": [{"message": "The search query is invalid"}],
                },
            )
        )
        result = CliInvoker().invoke(
            app,
            [
                "search",
                "repos",
                "uses: acme/reusable/.github/actions/build",
                "--repo",
                "acme/api",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 1
    assert "/search/repositories" in result.stderr
    assert "query text length" in result.stderr
    assert "Validation Failed" in result.stderr


def test_search_code_accepts_org_qualified_team_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    team_repos = [
        {"full_name": "acme/api"},
        {"full_name": "acme/web"},
    ]
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/teams/backend/repos").mock(
            return_value=httpx.Response(200, json=team_repos)
        )
        route = mock.get("/search/code").mock(return_value=httpx.Response(200, json={"items": []}))
        result = CliInvoker().invoke(
            app,
            ["search", "code", "TODO", "--team", "acme/backend", "--format", "json"],
        )

    assert result.exit_code == 0, result.output
    assert route.calls[0].request.url.params["q"] == "TODO (repo:acme/api OR repo:acme/web)"


def test_search_issues_accepts_org_qualified_team_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    team_repos = [
        {"full_name": "acme/api"},
        {"full_name": "acme/web"},
    ]
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/teams/backend/repos").mock(
            return_value=httpx.Response(200, json=team_repos)
        )
        route = mock.get("/search/issues").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        result = CliInvoker().invoke(
            app,
            [
                "search",
                "issues",
                "--team",
                "acme/backend",
                "--state",
                "open",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 0, result.output
    assert route.calls[0].request.url.params["q"] == "(repo:acme/api OR repo:acme/web) is:open"


def test_search_code_accepts_repeated_team_scopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/teams/backend/repos").mock(
            return_value=httpx.Response(200, json=[{"full_name": "acme/api"}])
        )
        mock.get("/orgs/platform/teams/ops/repos").mock(
            return_value=httpx.Response(200, json=[{"full_name": "platform/deploy"}])
        )
        route = mock.get("/search/code").mock(return_value=httpx.Response(200, json={"items": []}))
        result = CliInvoker().invoke(
            app,
            [
                "search",
                "code",
                "TODO",
                "--team",
                "acme/backend",
                "--team",
                "platform/ops",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 0, result.output
    assert route.calls[0].request.url.params["q"] == "TODO (repo:acme/api OR repo:platform/deploy)"


def test_search_code_expands_bare_team_with_single_org(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/teams/backend/repos").mock(
            return_value=httpx.Response(200, json=[{"full_name": "acme/api"}])
        )
        route = mock.get("/search/code").mock(return_value=httpx.Response(200, json={"items": []}))
        result = CliInvoker().invoke(
            app,
            [
                "search",
                "code",
                "TODO",
                "--org",
                "acme",
                "--team",
                "backend",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 0, result.output
    assert route.calls[0].request.url.params["q"] == "TODO org:acme repo:acme/api"


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
        result = CliInvoker().invoke(
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


def test_search_code_repeated_repos_render_or_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/search/code").mock(return_value=httpx.Response(200, json={"items": []}))
        result = CliInvoker().invoke(
            app,
            [
                "search",
                "code",
                "TODO",
                "--repo",
                "acme/api",
                "--repo",
                "acme/web",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 0, result.output
    assert route.calls[0].request.url.params["q"] == "TODO (repo:acme/api OR repo:acme/web)"


def test_search_code_reads_repo_scopes_from_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/search/code").mock(return_value=httpx.Response(200, json={"items": []}))
        result = CliInvoker().invoke(
            app,
            ["search", "code", "TODO", "--repo-stdin", "--format", "json"],
            input="acme/api\nacme/web\n",
        )

    assert result.exit_code == 0, result.output
    assert route.calls[0].request.url.params["q"] == "TODO (repo:acme/api OR repo:acme/web)"


def test_search_code_combines_explicit_and_stdin_repo_scopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/search/code").mock(return_value=httpx.Response(200, json={"items": []}))
        result = CliInvoker().invoke(
            app,
            [
                "search",
                "code",
                "TODO",
                "--repo",
                "acme/api",
                "--repo-stdin",
                "--format",
                "json",
            ],
            input="acme/web\n",
        )

    assert result.exit_code == 0, result.output
    assert route.calls[0].request.url.params["q"] == "TODO (repo:acme/api OR repo:acme/web)"


def test_search_repos_pipe_feeds_code_repo_stdin_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end proof of the typed pipe: ``search repos --format pipe`` emits
    self-describing ``github.repo`` envelopes, and ``search code --repo-stdin``
    reads them back, mapping each record's ``full_name`` into the repo scope."""
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    repos_payload = {
        "items": [
            {"id": 1, "name": "api", "full_name": "acme/api", "html_url": "https://x/acme/api"},
            {"id": 2, "name": "web", "full_name": "acme/web", "html_url": "https://x/acme/web"},
        ]
    }
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/search/repositories").mock(return_value=httpx.Response(200, json=repos_payload))
        code_route = mock.get("/search/code").mock(
            return_value=httpx.Response(200, json={"items": []})
        )

        produced = CliInvoker().invoke(
            app, ["search", "repos", "--org", "acme", "--format", "pipe"]
        )
        assert produced.exit_code == 0, produced.output
        lines = [line for line in produced.stdout.splitlines() if line.strip()]
        assert len(lines) == 2
        for line in lines:
            envelope = json.loads(line)
            assert envelope["untaped"] == "1"
            assert envelope["kind"] == "github.repo"
            assert "full_name" in envelope["record"]

        consumed = CliInvoker().invoke(
            app,
            ["search", "code", "TODO", "--repo-stdin", "--format", "json"],
            input=produced.stdout,
        )

    assert consumed.exit_code == 0, consumed.output
    assert code_route.calls[0].request.url.params["q"] == "TODO (repo:acme/api OR repo:acme/web)"


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
        result = CliInvoker().invoke(
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
    parsed = json.loads(result.stdout)
    assert parsed[0]["repo"] == "me/p"
    sent_q = route.calls[0].request.url.params["q"]
    for token in ("is:pr", "is:open", "label:bug", "user:@me"):
        assert token in sent_q


def test_search_issues_raw_repo_number_columns_are_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    payload = {
        "items": [
            {
                "id": 1,
                "number": 42,
                "title": "first",
                "state": "open",
                "html_url": "https://x",
                "repository_url": "https://api.github.com/repos/acme/api",
                "user": {"login": "octocat"},
            }
        ]
    }
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/search/issues").mock(return_value=httpx.Response(200, json=payload))
        result = CliInvoker().invoke(
            app,
            [
                "search",
                "issues",
                "--format",
                "raw",
                "--columns",
                "repo",
                "--columns",
                "number",
            ],
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "acme/api\t42"


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
        result = CliInvoker().invoke(
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
        result = CliInvoker().invoke(app, ["search", "repos", "--format", "json"])

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
        result = CliInvoker().invoke(app, ["search", "repos", "--limit", "2", "--format", "json"])

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
        result = CliInvoker().invoke(app, ["search", "repos", "--format", "json"])

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
        result = CliInvoker().invoke(
            app, ["search", "repos", "--limit", "1000", "--format", "json"]
        )

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert len(parsed) == 200


def test_search_repos_limit_above_1000_stops_at_github_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The CLI deliberately does NOT enforce a client-side max — passing
    # --limit 5000 is allowed and the paginator simply stops once
    # GitHub stops returning a `next` Link. Lock this stance so a
    # future contributor doesn't "tighten" the CLI with a max=1000
    # validator (which the issue body called out as out of scope).
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    items = [
        {"id": i, "name": f"r{i}", "full_name": f"me/r{i}", "html_url": "https://x"}
        for i in range(100)
    ]
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/search/repositories").mock(
            return_value=httpx.Response(200, json={"items": items})  # no Link header
        )
        result = CliInvoker().invoke(
            app, ["search", "repos", "--limit", "5000", "--format", "json"]
        )

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert len(parsed) == 100  # GitHub stopped sending more; we honour it.


def test_search_repos_limit_zero_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A zero limit is a usage error, not a synonym for "all results".
    # Pin a non-zero usage error + the range message so a
    # future routing change (e.g. swallowing 0 → empty result, exit 0)
    # fails loudly instead of silently passing the looser exit≠0 check.
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    result = CliInvoker().invoke(app, ["search", "repos", "--limit", "0"])
    assert result.exit_code != 0, result.output
    assert "--limit" in result.output
    assert ">= 1" in result.output


def test_search_repos_help_advertises_default_30(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    result = CliInvoker().invoke(app, ["search", "repos", "--help"])
    assert result.exit_code == 0, result.output
    # Help output includes the default and documented cap.
    assert "30" in result.output
    assert "1000" in result.output  # cap mentioned in the help string
    assert "exactly one --org" in result.output


def test_search_code_help_points_to_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    result = CliInvoker().invoke(app, ["search", "code", "--help"])
    assert result.exit_code == 0, result.output
    assert "Use sweep for exhaustive regex, path, negation" in result.output
    assert "multi-ref queries" in result.output
    assert "has no regex, caps at 1000 results" in result.output
    assert "searches the default branch only" in result.output


def test_search_code_default_limit_is_30(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # All four subcommands share SearchLimitOption — these tests pin
    # the contract per call site so a future refactor that hard-codes
    # a different default at one of the four would fail loudly.
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    items = [
        {
            "name": f"f{i}.py",
            "path": f"src/f{i}.py",
            "sha": f"sha{i}",
            "html_url": "https://x",
            "repository": {"full_name": "me/proj"},
        }
        for i in range(50)
    ]
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/search/code").mock(
            return_value=httpx.Response(200, json={"items": items})
        )
        result = CliInvoker().invoke(app, ["search", "code", "TODO", "--format", "json"])

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert len(parsed) == 30
    assert route.calls[0].request.url.params["per_page"] == "30"


def test_search_users_default_limit_is_30(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    items = [
        {
            "id": i,
            "login": f"u{i}",
            "type": "User",
            "html_url": f"https://github.com/u{i}",
        }
        for i in range(50)
    ]
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/search/users").mock(
            return_value=httpx.Response(200, json={"items": items})
        )
        result = CliInvoker().invoke(app, ["search", "users", "octocat", "--format", "json"])

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert len(parsed) == 30
    assert route.calls[0].request.url.params["per_page"] == "30"


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
        result = CliInvoker().invoke(app, ["search", "issues", "--format", "json"])

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert len(parsed) == 30
    assert route.calls[0].request.url.params["per_page"] == "30"


def test_search_team_without_org_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    result = CliInvoker().invoke(app, ["search", "repos", "--team", "backend"])
    assert result.exit_code != 0
    assert "ORG/SLUG" in result.output


def test_search_team_with_extra_path_segment_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    result = CliInvoker().invoke(app, ["search", "repos", "--team", "acme/backend/extra"])
    assert result.exit_code != 0
    assert "ORG/SLUG" in result.output


def test_search_team_slug_with_multiple_orgs_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    result = CliInvoker().invoke(
        app,
        ["search", "repos", "--org", "a", "--org", "b", "--team", "backend"],
    )
    assert result.exit_code != 0
    assert "ORG/SLUG" in result.output


_EMPTY_SEARCH = {"total_count": 0, "incomplete_results": False, "items": []}


def test_search_repos_empty_table_guides_with_stderr_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/search/repositories").mock(return_value=httpx.Response(200, json=_EMPTY_SEARCH))
        result = CliInvoker().invoke(app, ["search", "repos", "--language", "cobol"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "No repositories found" in result.stderr


def test_search_code_empty_table_guides_with_stderr_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/search/code").mock(return_value=httpx.Response(200, json=_EMPTY_SEARCH))
        result = CliInvoker().invoke(app, ["search", "code", "--language", "cobol"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "No code matches found" in result.stderr


def test_search_issues_empty_table_guides_with_stderr_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/search/issues").mock(return_value=httpx.Response(200, json=_EMPTY_SEARCH))
        result = CliInvoker().invoke(app, ["search", "issues", "--state", "open"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "No issues or pull requests found" in result.stderr


def test_search_users_empty_table_guides_with_stderr_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/search/users").mock(return_value=httpx.Response(200, json=_EMPTY_SEARCH))
        result = CliInvoker().invoke(app, ["search", "users", "zzunlikelyhandle"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "No users or organizations found" in result.stderr


def test_search_empty_json_stays_pipe_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/search/repositories").mock(return_value=httpx.Response(200, json=_EMPTY_SEARCH))
        result = CliInvoker().invoke(
            app, ["search", "repos", "--language", "cobol", "--format", "json"]
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "[]"
    assert "No repositories found" not in result.stderr


def test_search_repos_reports_progress_on_stderr(
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
            }
        ],
    }
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/search/repositories").mock(return_value=httpx.Response(200, json=payload))
        result = CliInvoker().invoke(
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
    # Progress announces on stderr; the data channel stays clean.
    assert "Searching repositories" in result.stderr
    assert result.stdout.splitlines() == ["octocat/alpha"]
    assert "Searching repositories" not in result.stdout


def test_search_with_invalid_theme_still_runs_and_reports_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The progress UiContext is built strict=False, so a misconfigured theme
    # degrades the spinner to the default theme instead of failing an
    # otherwise-valid search on the data path.
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_missing_theme_config(tmp_path)))
    payload = {
        "total_count": 1,
        "incomplete_results": False,
        "items": [
            {
                "id": 1,
                "name": "alpha",
                "full_name": "octocat/alpha",
                "html_url": "https://github.com/octocat/alpha",
            }
        ],
    }
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/search/repositories").mock(return_value=httpx.Response(200, json=payload))
        result = CliInvoker().invoke(
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
    assert result.stdout.splitlines() == ["octocat/alpha"]
    assert "Searching repositories" in result.stderr
