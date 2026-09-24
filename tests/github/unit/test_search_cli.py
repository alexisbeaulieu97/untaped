"""End-to-end CLI tests for ``untaped github search`` (HTTP mocked via respx)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from untaped.bootstrap import build_root_app
from untaped.capabilities.github.cli import app
from untaped.settings import get_settings
from untaped.testing import CliInvoker, CliResult, invoke_cli

API = "https://api.github.com"
ENDPOINTS = {
    "repos": "/search/repositories",
    "code": "/search/code",
    "issues": "/search/issues",
    "users": "/search/users",
}
TEAMS = {
    "/orgs/acme/teams/backend/repos": [{"full_name": "acme/api"}, {"full_name": "acme/web"}],
    "/orgs/platform/teams/ops/repos": [{"full_name": "platform/deploy"}],
}


@pytest.fixture(autouse=True)
def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path, monkeypatch)


def _write_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ui: str = "") -> None:
    cfg = tmp_path / "config.yml"
    ui_section = f"    ui:\n      {ui}\n" if ui else ""
    cfg.write_text(f"profiles:\n  default:\n{ui_section}    github:\n      token: ghp_test\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()


def _repo(i: int | str) -> dict[str, Any]:
    return {"id": 1, "name": f"r{i}", "full_name": f"me/r{i}", "html_url": f"https://x/r{i}"}


def _code(i: int) -> dict[str, Any]:
    path = f"src/f{i}.py"
    return {
        "name": path,
        "path": path,
        "sha": "s",
        "html_url": f"https://x/{path}",
        "repository": {"full_name": "me/proj"},
    }


def _issue(i: int, repo: str = "me/p") -> dict[str, Any]:
    return {
        "id": i,
        "number": i,
        "title": f"t{i}",
        "state": "open",
        "html_url": "https://x",
        "repository_url": f"{API}/repos/{repo}",
        "user": {"login": "octocat"},
    }


def _user(i: int) -> dict[str, Any]:
    return {
        "id": i,
        "login": f"u{i}",
        "type": "User",
        "html_url": f"https://github.com/u{i}",
        "url": f"{API}/users/u{i}",
    }


ROW = {"repos": _repo, "code": _code, "issues": _issue, "users": _user}


def _search(
    args: Sequence[str],
    *,
    items: Sequence[dict[str, Any]] = (),
    pages: Sequence[Sequence[dict[str, Any]]] = (),
    input: str | None = None,
) -> tuple[CliResult, respx.Route]:
    """Run ``search <args>`` against a mocked API; ``pages`` chain via ``Link`` headers."""
    endpoint = ENDPOINTS[args[0]]
    with respx.mock(base_url=API, assert_all_called=False) as mock:
        for path, repos in TEAMS.items():
            mock.get(path).mock(return_value=httpx.Response(200, json=repos))
        for number, page in enumerate(pages[1:], start=2):
            link = {"Link": f'<{API}{endpoint}?page={number + 1}>; rel="next"'}
            mock.get(endpoint, params={"page": str(number)}).mock(
                return_value=httpx.Response(
                    200, json={"items": list(page)}, headers=link if number < len(pages) else {}
                )
            )
        first = {"Link": f'<{API}{endpoint}?page=2>; rel="next"'} if len(pages) > 1 else {}
        route = mock.get(endpoint).mock(
            return_value=httpx.Response(
                200, json={"items": list(pages[0] if pages else items)}, headers=first
            )
        )
        result = CliInvoker().invoke(app, ["search", *args], input=input)
    return result, route


def _q(route: respx.Route) -> str:
    return str(route.calls[0].request.url.params["q"])


@pytest.mark.parametrize(
    ("args", "stdin", "expected_q"),
    [
        (["repos", "--language", "python"], None, "user:@me language:python"),
        (["repos", "--org", "acme"], None, "org:acme"),
        (["repos", "--team", "acme/backend"], None, "(repo:acme/api OR repo:acme/web)"),
        (["code", "TODO", "--language", "python"], None, "TODO user:@me language:python"),
        (
            ["code", "TODO", "--team", "acme/backend", "--team", "platform/ops"],
            None,
            "TODO (repo:acme/api OR repo:acme/web OR repo:platform/deploy)",
        ),
        (
            ["code", "TODO", "--org", "acme", "--team", "backend"],
            None,
            "TODO org:acme (repo:acme/api OR repo:acme/web)",
        ),
        (
            ["code", "TODO", "--repo", "acme/api", "--repo", "acme/web"],
            None,
            "TODO (repo:acme/api OR repo:acme/web)",
        ),
        (
            ["code", "TODO", "--stdin"],
            "acme/api\nacme/web\n",
            "TODO (repo:acme/api OR repo:acme/web)",
        ),
        (
            ["code", "TODO", "--repo", "acme/api", "--stdin"],
            "acme/web\n",
            "TODO (repo:acme/api OR repo:acme/web)",
        ),
        (
            ["issues", "--team", "acme/backend", "--state", "open"],
            None,
            "(repo:acme/api OR repo:acme/web) is:open",
        ),
        (
            ["issues", "--state", "open", "--kind", "pr", "--label", "bug"],
            None,
            "user:@me is:pr is:open label:bug",
        ),
        # User search ignores scope qualifiers, so it never injects user:@me.
        (["users", "--kind", "org", "--location", "montreal"], None, "type:org location:montreal"),
    ],
)
def test_search_flags_and_scopes_build_the_github_query(
    args: list[str], stdin: str | None, expected_q: str
) -> None:
    result, route = _search([*args, "--format", "json"], input=stdin)

    assert result.exit_code == 0, result.output
    assert _q(route) == expected_q
    assert "sort" not in route.calls[0].request.url.params


@pytest.mark.parametrize("kind", list(ENDPOINTS))
def test_search_defaults_to_30_results_per_command(kind: str) -> None:
    # A bare search costs one request of the user's 30/min search budget.
    items = [ROW[kind](i) for i in range(50)]

    result, route = _search([kind, "q", "--format", "json"], items=items)

    assert result.exit_code == 0, result.output
    assert len(json.loads(result.stdout)) == 30
    assert route.calls[0].request.url.params["per_page"] == "30"
    assert route.call_count == 1


def test_search_limit_caps_results() -> None:
    result, _ = _search(
        ["repos", "--limit", "2", "--format", "json"], items=[_repo(i) for i in range(5)]
    )

    assert result.exit_code == 0, result.output
    assert len(json.loads(result.stdout)) == 2


@pytest.mark.parametrize("limit", ["1000", "5000"])
def test_search_large_limit_follows_link_pages_until_github_stops(limit: str) -> None:
    # --limit 1000 is the documented escape hatch; above it there is no
    # client-side max: pagination simply ends when GitHub stops sending `next`.
    pages = [[_repo(i) for i in range(100)], [_repo(i) for i in range(100, 200)]]

    result, _ = _search(["repos", "--limit", limit, "--format", "json"], pages=pages)

    assert result.exit_code == 0, result.output
    assert [row["full_name"] for row in json.loads(result.stdout)] == [
        f"me/r{i}" for i in range(200)
    ]


def test_search_limit_zero_is_a_usage_error() -> None:
    result = CliInvoker().invoke(app, ["search", "repos", "--limit", "0"])

    assert result.exit_code == 2, result.output
    assert "--limit" in result.output
    assert ">= 1" in result.output


def test_search_help_documents_limits_and_points_code_search_to_sweep() -> None:
    repos = CliInvoker().invoke(app, ["search", "repos", "--help"])
    code = CliInvoker().invoke(app, ["search", "code", "--help"])

    assert repos.exit_code == code.exit_code == 0
    assert "30" in repos.output
    assert "1000" in repos.output
    assert "exactly one --org" in repos.output
    assert "Use sweep for exhaustive regex, path, negation" in code.output
    assert "multi-ref queries" in code.output
    assert "has no regex, caps at 1000 results" in code.output
    assert "searches the default branch only" in code.output


@pytest.mark.parametrize(
    "team_args",
    [
        ["--team", "backend"],
        ["--team", "acme/backend/extra"],
        ["--org", "a", "--org", "b", "--team", "backend"],
    ],
)
def test_search_malformed_team_is_a_usage_error(team_args: list[str]) -> None:
    result = CliInvoker().invoke(app, ["search", "repos", *team_args])

    assert result.exit_code == 2, result.output
    assert "ORG/SLUG" in result.output


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["repos", "--language", "cobol"], "No repositories found"),
        (["code", "--language", "cobol"], "No code matches found"),
        (["issues", "--state", "open"], "No issues or pull requests found"),
        (["users", "zzunlikelyhandle"], "No users or organizations found"),
    ],
)
def test_search_empty_table_hints_on_stderr_but_json_stays_clean(
    args: list[str], message: str
) -> None:
    table, _ = _search(args)
    as_json, _ = _search([*args, "--format", "json"])

    assert table.exit_code == as_json.exit_code == 0
    assert table.stdout == ""
    assert message in table.stderr
    assert as_json.stdout.strip() == "[]"
    assert message not in as_json.stderr


def test_search_repos_raw_prints_first_field_and_progress_goes_to_stderr() -> None:
    result, _ = _search(["repos", "--format", "raw"], items=[_repo("alpha")])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["me/ralpha"]
    assert "Searching repositories" in result.stderr


def test_search_with_invalid_theme_still_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Progress uses strict=False, so a bad theme degrades to the default theme
    # instead of failing an otherwise-valid search on the data path.
    _write_config(tmp_path, monkeypatch, ui="theme: missing")

    result, _ = _search(["repos", "--format", "raw"], items=[_repo("alpha")])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["me/ralpha"]
    assert "\x1b[" not in result.output
    assert "unknown UI theme" not in result.output


def test_search_repos_table_honors_list_collection_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path, monkeypatch, ui="collection_view: list")

    result, _ = _search(["repos", "--format", "table"], items=[_repo("alpha")])

    assert result.exit_code == 0, result.output
    assert "full_name: me/ralpha" in result.stdout
    assert "│" not in result.stdout


def test_search_issues_raw_repo_number_columns_are_actionable() -> None:
    result, _ = _search(
        ["issues", "--format", "raw", "--columns", "repo", "--columns", "number"],
        items=[_issue(42, repo="acme/api")],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "acme/api\t42"


def test_search_users_pipe_tags_user_hit_with_web_url() -> None:
    result, _ = _search(["users", "u1", "--format", "pipe"], items=[_user(1)])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.splitlines()[0])
    assert envelope["kind"] == "github.user_hit"
    assert envelope["record"]["url"] == "https://github.com/u1"


def test_search_repos_pipe_feeds_code_stdin_round_trip() -> None:
    """``search repos --format pipe`` emits ``github.repo_hit`` envelopes that
    ``search code --stdin`` reads back into its repo scope."""
    repos = [
        {"id": 1, "name": "api", "full_name": "acme/api", "html_url": "https://x/acme/api"},
        {"id": 2, "name": "web", "full_name": "acme/web", "html_url": "https://x/acme/web"},
    ]
    produced, _ = _search(["repos", "--org", "acme", "--format", "pipe"], items=repos)
    envelopes = [json.loads(line) for line in produced.stdout.splitlines() if line.strip()]

    consumed, route = _search(
        ["code", "TODO", "--stdin", "--format", "json"], input=produced.stdout
    )

    assert [(env["untaped"], env["kind"]) for env in envelopes] == [("1", "github.repo_hit")] * 2
    assert consumed.exit_code == 0, consumed.output
    assert _q(route) == "TODO (repo:acme/api OR repo:acme/web)"


def test_search_stdin_rejects_records_of_another_kind() -> None:
    envelope = json.dumps({"untaped": "1", "kind": "github.user", "record": {"login": "x"}})

    result, _ = _search(["code", "TODO", "--stdin", "--format", "json"], input=f"{envelope}\n")

    assert result.exit_code == 2, result.output
    assert "github.user" in result.stderr


def test_search_repo_stdin_is_a_deprecated_alias_of_stdin() -> None:
    with respx.mock(base_url=API) as mock:
        route = mock.get("/search/code").mock(return_value=httpx.Response(200, json={"items": []}))
        result = invoke_cli(
            build_root_app(externals=[]),
            ["github", "search", "code", "TODO", "--repo-stdin", "--format", "json"],
            input="acme/api\n",
        )

    assert result.exit_code == 0, result.output
    assert "`--repo-stdin` is deprecated" in result.stderr
    assert _q(route) == "TODO repo:acme/api"


def test_search_repos_rejects_oversized_query_before_http_and_explains_422() -> None:
    oversized, route = _search(["repos", "x" * 257, "--format", "json"])
    assert oversized.exit_code == 1
    assert route.call_count == 0
    assert "query text length 257 exceeds 256; narrow" in oversized.stderr

    with respx.mock(base_url=API) as mock:
        mock.get("/search/repositories").mock(
            return_value=httpx.Response(
                422,
                json={
                    "message": "Validation Failed",
                    "errors": [{"message": "The search query is invalid"}],
                },
            )
        )
        rejected = CliInvoker().invoke(
            app, ["search", "repos", "uses: a/b", "--repo", "acme/api", "--format", "json"]
        )

    assert rejected.exit_code == 1
    assert "/search/repositories" in rejected.stderr
    assert "query text length" in rejected.stderr
    assert "Validation Failed" in rejected.stderr
