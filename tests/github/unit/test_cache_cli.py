"""End-to-end CLI tests for ``untaped github cache`` against real local Git repos."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import respx

from untaped.capabilities.github.cli import app
from untaped.testing import CliInvoker, CliResult, assert_destructive_contract

SourceRepo = Callable[[str, dict[str, str | bytes]], Path]


@pytest.fixture(autouse=True)
def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "profiles:\n  default:\n    github:\n      token: ghp_test\n"
        f"      corpus_path: {tmp_path / 'corpus'}\n"
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    return cfg


def _repo(full_name: str, source: Path, *, archived: bool = False) -> dict[str, object]:
    return {
        "full_name": full_name,
        "name": full_name.rsplit("/", 1)[1],
        "html_url": f"https://github.com/{full_name}",
        "clone_url": source.as_uri(),
        "default_branch": "main",
        "archived": archived,
        "pushed_at": "2026-07-01T00:00:00Z",
    }


def _cache(args: list[str], *, org: dict[str, list[dict[str, object]]] | None = None) -> CliResult:
    """Run ``<args>``; ``org`` maps an org name to its ``/orgs/<org>/repos`` listing."""
    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        for name, repos in (org or {}).items():
            mock.get(f"/orgs/{name}/repos").mock(return_value=httpx.Response(200, json=repos))
        return CliInvoker().invoke(app, args)


def _rows(result: CliResult) -> list[str]:
    assert result.exit_code == 0, result.output
    return [row["repo"] for row in json.loads(result.stdout)]


def _cached() -> list[str]:
    return _rows(CliInvoker().invoke(app, ["cache", "status", "--format", "json"]))


def _populate(source_repo: SourceRepo, *full_names: str) -> dict[str, dict[str, object]]:
    listings: dict[str, dict[str, object]] = {}
    for full_name in full_names:
        org, name = full_name.split("/")
        listings[full_name] = _repo(full_name, source_repo(name, {"README.md": "hello\n"}))
        sync = ["cache", "sync", "--org", org, "--format", "json"]
        assert _rows(_cache(sync, org={org: [listings[full_name]]})) == [full_name]
    return listings


def test_cache_status_reports_profile_size_and_freshness(source_repo: SourceRepo) -> None:
    _populate(source_repo, "acme/api")

    as_json = CliInvoker().invoke(app, ["cache", "status", "--format", "json"])
    table = CliInvoker().invoke(app, ["cache", "status"])

    [row] = json.loads(as_json.stdout)
    assert (row["repo"], row["profile"]) == ("acme/api", "default")
    assert row["disk_bytes"] > 0
    assert re.search(
        r"Cache: 1 repo, [0-9.]+ KiB, oldest just now, newest just now", as_json.stderr
    )
    assert "KiB" in table.stdout
    assert "just now" in table.stdout
    assert "disk_bytes" not in table.stdout


def test_cache_sync_warms_the_corpus_without_a_query(source_repo: SourceRepo) -> None:
    listed = [_repo("acme/api", source_repo("api", {"README.md": "hello\n"}))]

    def sync(*extra: str) -> dict[str, object]:
        result = _cache(
            ["cache", "sync", "--org", "acme", "--format", "json", *extra], org={"acme": listed}
        )
        assert result.exit_code == 0, result.output
        assert result.stderr.splitlines()[-1].startswith("sync: 1 ")
        [row] = json.loads(result.stdout)
        return dict(row)

    first, second, forced = sync(), sync(), sync("--refresh")

    assert list(first) == ["repo", "action", "fetched_at", "detail"]
    assert (first["repo"], first["action"]) == ("acme/api", "synced")
    assert second["action"] == "skipped"
    assert forced["action"] == "synced"
    assert _cached() == ["acme/api"]


def test_cache_sync_skips_fetch_when_github_reports_no_push(
    _config: Path, source_repo: SourceRepo
) -> None:
    _config.write_text(_config.read_text() + "      sweep:\n        max_age_seconds: 0\n")
    listed = [_repo("acme/api", source_repo("api", {"README.md": "hello\n"}))]
    args = ["cache", "sync", "--org", "acme", "-f", "json"]

    actions = [json.loads(_cache(args, org={"acme": listed}).stdout)[0]["action"] for _ in range(2)]

    assert actions == ["synced", "unchanged"]


def test_cache_sync_of_piped_repos_list_skips_fetch_when_github_reports_no_push(
    _config: Path, source_repo: SourceRepo
) -> None:
    _config.write_text(_config.read_text() + "      sweep:\n        max_age_seconds: 0\n")
    listed = [_repo("acme/api", source_repo("api", {"README.md": "hello\n"}))]
    piped = _cache(["repos", "list", "--org", "acme", "-f", "pipe"], org={"acme": listed})
    args = ["cache", "sync", "--stdin", "-f", "json"]

    # The first fetch stores GitHub's own pushed_at; piped records must match it.
    synced = _cache(["cache", "sync", "--org", "acme", "-f", "json"], org={"acme": listed})
    actions = [
        json.loads(CliInvoker().invoke(app, args, input=piped.stdout).stdout)[0]["action"]
        for _ in range(2)
    ]

    assert '"pushed_at": "2026-07-01T00:00:00Z"' in piped.stdout
    assert json.loads(synced.stdout)[0]["action"] == "synced"
    assert actions == ["unchanged", "unchanged"]


def test_cache_sync_sends_the_token_only_to_the_enterprise_git_host(
    _config: Path, git_auth: dict[str, str | None]
) -> None:
    _config.write_text(_config.read_text() + "      base_url: https://ghe.example/api/v3\n")
    records = [
        {"untaped": "1", "kind": "github.repo", "record": {**row, "default_branch": "main"}}
        for row in (
            {"repo": "acme/api", "clone_url": "https://ghe.example/acme/api.git"},
            {"repo": "acme/web", "clone_url": "https://other.example/acme/web.git"},
        )
    ]

    result = CliInvoker().invoke(
        app,
        ["cache", "sync", "--stdin", "-f", "json"],
        input="".join(f"{json.dumps(record)}\n" for record in records),
    )

    assert result.exit_code == 0, result.output
    assert git_auth["https://ghe.example/acme/api.git"] is not None
    assert git_auth["https://other.example/acme/web.git"] is None


def test_cache_sync_failure_exits_1_and_names_the_repo(tmp_path: Path) -> None:
    listed = [_repo("acme/gone", tmp_path / "missing")]

    result = _cache(["cache", "sync", "--org", "acme", "-f", "json"], org={"acme": listed})

    assert result.exit_code == 1, result.output
    [row] = json.loads(result.stdout)
    assert row["action"] == "failed"
    assert row["error"]["message"] == row["detail"]
    assert (row["error"]["category"], row["error"]["system"]) == ("failed", "git")
    assert f"error: acme/gone: {row['detail']}" in result.stderr
    assert "sync: 1 failed" in result.stderr


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            ["sync"],
            "cache sync requires --org, --team, --repo, --stdin, or a github.default_org setting",
        ),
        (["delete"], "cache delete requires REPO arguments or --all"),
        (["delete", "acme/api", "--all", "--yes"], "pass REPO arguments or --all, not both"),
        (["prune", "--yes"], "cache prune requires --org"),
        (["prune", "--team", "acme/backend", "--yes"], "--team"),
        (["clean", "--all", "--yes"], "clean"),
        (["sync", "--org", "acme", "--archived"], "--archived"),
    ],
)
def test_cache_selection_usage_errors_exit_2(args: list[str], message: str) -> None:
    result = CliInvoker().invoke(app, ["cache", *args])

    assert result.exit_code == 2, result.output
    assert message in result.output


@pytest.mark.parametrize(
    ("args", "deleted", "remaining"),
    [
        (["--all", "--org", "ACME"], ["acme/api"], ["other/tool"]),
        (["ACME/Api"], ["acme/api"], ["other/tool"]),
        (["--all"], ["acme/api", "other/tool"], []),
        (["--all", "--dry-run"], ["acme/api", "other/tool"], ["acme/api", "other/tool"]),
    ],
    ids=["all-in-org", "case-insensitive", "all", "dry-run"],
)
def test_cache_delete_selects_cached_repos(
    source_repo: SourceRepo, args: list[str], deleted: list[str], remaining: list[str]
) -> None:
    _populate(source_repo, "acme/api", "other/tool")

    result = CliInvoker().invoke(app, ["cache", "delete", *args, "--yes", "--format", "json"])

    assert _rows(result) == deleted
    assert _cached() == remaining


@pytest.mark.parametrize(
    ("args", "missing"),
    [
        (["acme/typo"], "acme/typo"),
        (["acme/api", "acme/typo"], "acme/typo"),
        (["acme/api", "--org", "other"], "acme/api"),
    ],
    ids=["typo", "mixed", "outside-org"],
)
def test_cache_delete_of_an_uncached_repo_fails_before_deleting(
    source_repo: SourceRepo, args: list[str], missing: str
) -> None:
    _populate(source_repo, "acme/api")

    result = CliInvoker().invoke(app, ["cache", "delete", *args, "--yes", "--format", "json"])

    assert result.exit_code == 1, result.output
    assert f"error: cached repo not found: '{missing}'" in result.stderr
    assert result.stdout == ""
    assert _cached() == ["acme/api"]


def test_cache_delete_repo_conforms_to_destructive_contract(source_repo: SourceRepo) -> None:
    _populate(source_repo, "acme/api")

    def corpus_still_has_repo() -> None:
        assert _cached() == ["acme/api"]

    assert_destructive_contract(
        app,
        ["cache", "delete", "acme/api", "--format", "json"],
        assert_unchanged=corpus_still_has_repo,
    )


def test_cache_prune_deletes_departed_and_archived_repos(source_repo: SourceRepo) -> None:
    listings = _populate(source_repo, "acme/api", "acme/old", "acme/worker")
    live = {"acme": [listings["acme/api"], {**listings["acme/old"], "archived": True}]}
    args = ["cache", "prune", "--org", "acme", "--format", "json"]

    refused = _cache(args, org=live)
    kept = _cached()
    pruned = _cache([*args, "--yes"], org=live)

    assert refused.exit_code == 2
    assert "requires --yes" in refused.stderr
    assert kept == ["acme/api", "acme/old", "acme/worker"]
    assert _rows(pruned) == ["acme/old", "acme/worker"]
    assert _cached() == ["acme/api"]


@pytest.mark.usefixtures("default_org")
def test_cache_prune_falls_back_to_github_default_org(source_repo: SourceRepo) -> None:
    listings = _populate(source_repo, "acme/api", "acme/old")

    pruned = _cache(
        ["cache", "prune", "--yes", "--format", "json"], org={"acme": [listings["acme/api"]]}
    )

    assert _rows(pruned) == ["acme/old"]


@pytest.mark.parametrize(
    ("args", "synced"),
    [
        ([], ["acme/api"]),
        (["--archived", "only"], ["acme/old"]),
        (["--archived", "include"], ["acme/api", "acme/old"]),
    ],
    ids=["default-excludes", "only", "include"],
)
@pytest.mark.usefixtures("default_org")
def test_cache_sync_archived_modes_and_default_org(
    source_repo: SourceRepo, args: list[str], synced: list[str]
) -> None:
    listing = [
        _repo("acme/api", source_repo("api", {"README.md": "x\n"})),
        _repo("acme/old", source_repo("old", {"README.md": "x\n"}), archived=True),
    ]

    result = _cache(["cache", "sync", *args, "--format", "json"], org={"acme": listing})

    assert _rows(result) == synced


def test_cache_worktree_materializes_cached_ref(source_repo: SourceRepo) -> None:
    _populate(source_repo, "acme/api")

    result = CliInvoker().invoke(app, ["cache", "worktree", "acme/api", "--format", "json"])

    assert result.exit_code == 0, result.output
    row = json.loads(result.stdout)
    assert row["repo"] == "acme/api"
    assert (Path(row["path"]) / "README.md").is_file()


@pytest.mark.parametrize(
    ("repo", "message"),
    [
        ("acme/missing", "repository is not in the local corpus"),
        ("acme", "repository must be owner/name: 'acme'"),
    ],
)
def test_cache_worktree_rejects_repos_it_cannot_materialize(repo: str, message: str) -> None:
    result = CliInvoker().invoke(app, ["cache", "worktree", repo])

    assert result.exit_code == 1, result.output
    assert message in result.stderr
