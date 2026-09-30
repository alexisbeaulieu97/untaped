"""End-to-end CLI tests for ``untaped github repos``."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from untaped.capabilities.github.cli import app
from untaped.testing import CliInvoker, CliResult


@pytest.fixture(autouse=True)
def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("profiles:\n  default:\n    github:\n      token: ghp_test\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))


def _repo(full_name: str, *, archived: bool = False, fork: bool = False) -> dict[str, object]:
    return {
        "full_name": full_name,
        "name": full_name.rsplit("/", 1)[1],
        "html_url": f"https://github.com/{full_name}",
        "clone_url": f"https://github.com/{full_name}.git",
        "ssh_url": f"git@github.com:{full_name}.git",
        "default_branch": "main",
        "archived": archived,
        "fork": fork,
    }


LISTINGS = {
    "/orgs/acme/repos": [
        _repo("acme/zeta"),
        _repo("acme/play-api"),
        _repo("acme/play-old", archived=True),
        _repo("acme/play-fork", fork=True),
    ],
    "/orgs/acme/teams/backend/repos": [_repo("acme/play-team")],
    "/orgs/platform/teams/ops/repos": [_repo("platform/play-role"), _repo("acme/play-api")],
}


def _list(*args: str) -> CliResult:
    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        for path, repos in LISTINGS.items():
            mock.get(path).mock(return_value=httpx.Response(200, json=repos))
        return CliInvoker().invoke(app, ["repos", "list", *args, "--format", "raw", "-c", "repo"])


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (
            [
                "play*",
                "--org",
                "acme",
                "--team",
                "platform/ops",
                "--archived",
                "exclude",
                "--no-fork",
            ],
            ["acme/play-api", "platform/play-role"],
        ),
        (["play*", "--team", "acme/backend"], ["acme/play-team"]),
        # A bare team with exactly one --org adds that team's repos to the org's.
        (
            ["play*", "--org", "acme", "--team", "backend", "--no-fork", "--archived", "include"],
            ["acme/play-api", "acme/play-old", "acme/play-team"],
        ),
        (["--org", "acme", "--limit", "2"], ["acme/play-api", "acme/play-fork"]),
    ],
    ids=["scopes-and-filters", "qualified-team", "bare-team-is-additive", "limit-after-sort"],
)
def test_repos_list_combines_scopes_and_filters_into_sorted_rows(
    args: list[str], expected: list[str]
) -> None:
    result = _list(*args)

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == expected
    assert "Listing repositories" in result.stderr


@pytest.mark.parametrize(
    ("archived", "expected"),
    [
        ([], ["acme/play-api", "acme/play-fork", "acme/zeta"]),
        (["--archived", "exclude"], ["acme/play-api", "acme/play-fork", "acme/zeta"]),
        (
            ["--archived", "include"],
            ["acme/play-api", "acme/play-fork", "acme/play-old", "acme/zeta"],
        ),
        (["--archived", "only"], ["acme/play-old"]),
    ],
    ids=["default-excludes", "exclude", "include", "only"],
)
def test_repos_list_archived_is_include_exclude_or_only(
    archived: list[str], expected: list[str]
) -> None:
    result = _list("--org", "acme", *archived)

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == expected


@pytest.mark.parametrize("old", [["--no-archived"], ["--archived"], ["--archived", "yes"]])
def test_repos_list_rejects_the_old_boolean_archived_spellings(old: list[str]) -> None:
    result = CliInvoker().invoke(app, ["repos", "list", "--org", "acme", *old])

    assert result.exit_code == 2, result.output


def test_repos_list_limit_prints_a_truncation_notice_on_stderr() -> None:
    truncated = _list("--org", "acme", "--limit", "2")
    complete = _list("--org", "acme", "--limit", "3")

    assert truncated.stdout.splitlines() == ["acme/play-api", "acme/play-fork"]
    assert "showing 2 of 3 repositories; omit --limit to list all" in truncated.stderr
    assert "showing" not in complete.stderr


@pytest.mark.usefixtures("default_org")
def test_repos_list_falls_back_to_github_default_org() -> None:
    unscoped = _list("play*")
    team_only = _list("play*", "--team", "acme/backend")

    assert unscoped.stdout.splitlines() == ["acme/play-api", "acme/play-fork"]
    # Any explicit scope replaces the default org rather than adding to it.
    assert team_only.stdout.splitlines() == ["acme/play-team"]


def test_repos_list_pipe_record_carries_kind_urls_and_repo() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=[_repo("acme/a")]))
        result = CliInvoker().invoke(app, ["repos", "list", "--org", "acme", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    [envelope] = [json.loads(line) for line in result.stdout.splitlines()]
    record = envelope["record"]
    assert (envelope["untaped"], envelope["kind"]) == ("1", "github.repo")
    assert record["repo"] == "acme/a"
    assert record["url"] == "https://github.com/acme/a"
    assert not {"full_name", "html_url", "name"} & set(record)
    assert record["ssh_url"] == "git@github.com:acme/a.git"


def test_repos_list_table_shows_default_columns_and_json_every_field() -> None:
    listed = [{**_repo("acme/a"), "pushed_at": "2026-07-01T00:00:00Z"}]
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=listed))
        table = CliInvoker().invoke(app, ["repos", "list", "--org", "acme"])
        as_json = CliInvoker().invoke(app, ["repos", "list", "--org", "acme", "-f", "json"])

    assert table.exit_code == 0, table.output
    assert "repo" in table.stdout
    assert "full_name" not in table.stdout
    assert "pushed_at" not in table.stdout
    assert "ssh_url" not in table.stdout
    [row] = json.loads(as_json.stdout)
    assert row["pushed_at"] == "2026-07-01T00:00:00Z"


@pytest.mark.parametrize(
    ("args", "messages"),
    [
        (["play*"], ["requires --org or --team", "github.default_org"]),
        (["--org", "acme", "--regex"], ["--regex requires PATTERN"]),
        (["[", "--org", "acme", "--regex"], ["invalid regular expression"]),
        (["--team", "backend"], ["ORG/SLUG"]),
        (["--org", "acme", "--org", "platform", "--team", "backend"], ["exactly one --org"]),
    ],
)
def test_repos_list_usage_errors_exit_2(args: list[str], messages: list[str]) -> None:
    result = CliInvoker().invoke(app, ["repos", "list", *args])

    assert result.exit_code == 2
    assert all(message in result.output for message in messages)


def test_repos_list_help_documents_pattern_targeting() -> None:
    result = CliInvoker().invoke(app, ["repos", "list", "--help"])

    assert result.exit_code == 0, result.output
    for phrase in ("glob", "owner/name", "unanchored", "additive", "exactly one --org", "--regex"):
        assert phrase in result.output
