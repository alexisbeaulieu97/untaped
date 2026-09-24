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
            ["play*", "--org", "acme", "--team", "platform/ops", "--no-archived", "--no-fork"],
            ["acme/play-api", "platform/play-role"],
        ),
        (["play*", "--team", "acme/backend"], ["acme/play-team"]),
        # A bare team with exactly one --org adds that team's repos to the org's.
        (
            ["play*", "--org", "acme", "--team", "backend", "--no-fork"],
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


def test_repos_list_pipe_record_carries_kind_urls_and_repo() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=[_repo("acme/a")]))
        result = CliInvoker().invoke(app, ["repos", "list", "--org", "acme", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    [envelope] = [json.loads(line) for line in result.stdout.splitlines()]
    record = envelope["record"]
    assert (envelope["untaped"], envelope["kind"]) == ("1", "github.repo")
    assert record["repo"] == record["full_name"] == "acme/a"
    assert record["url"] == record["html_url"] == "https://github.com/acme/a"
    assert record["ssh_url"] == "git@github.com:acme/a.git"


@pytest.mark.parametrize(
    ("args", "messages"),
    [
        (["play*"], ["requires --org or --team", "user-owned"]),
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
    for phrase in ("glob", "full_name", "unanchored", "additive", "exactly one --org", "--regex"):
        assert phrase in result.output
