"""End-to-end CLI tests for ``untaped github cache``."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

from untaped.capabilities.github.cli import app
from untaped.capabilities.github.settings import GithubSettings
from untaped.settings import get_settings, register_profile_settings
from untaped.testing import CliInvoker, assert_destructive_contract


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    register_profile_settings("github", GithubSettings)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.yml"
    corpus = tmp_path / "corpus"
    cfg.write_text(
        f"profiles:\n  default:\n    github:\n      token: ghp_test\n      corpus_path: {corpus}\n"
    )
    return cfg


def _git(cwd: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _source_repo(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "a@example.com")
    _git(repo, "config", "user.name", "A")
    _git(repo, "config", "commit.gpgsign", "false")
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "branch", "-M", "main")
    return repo


def _repo(full_name: str, source: Path, *, archived: bool = False) -> dict[str, object]:
    name = full_name.rsplit("/", 1)[1]
    return {
        "full_name": full_name,
        "name": name,
        "html_url": f"https://github.com/{full_name}",
        "clone_url": source.as_uri(),
        "ssh_url": f"git@github.com:{full_name}.git",
        "default_branch": "main",
        "private": True,
        "archived": archived,
        "fork": False,
    }


def _populate_cache(tmp_path: Path, repos: list[dict[str, object]], *, org: str = "acme") -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get(f"/orgs/{org}/repos").mock(return_value=httpx.Response(200, json=repos))
        result = CliInvoker().invoke(
            app,
            ["sweep", "--org", org, "--has-file", "README.md", "--format", "json"],
        )
    assert result.exit_code == 0, result.output


def test_cache_status_reports_profile_disk_freshness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    source = _source_repo(tmp_path, "api", {"README.md": "hello\n"})
    _populate_cache(tmp_path, [_repo("acme/api", source)])

    result = CliInvoker().invoke(app, ["cache", "status", "--format", "json"])

    assert result.exit_code == 0, result.output
    [row] = json.loads(result.stdout)
    assert row["repo"] == "acme/api"
    assert row["profile"] == "default"
    assert row["disk_bytes"] > 0
    assert re.search(r"Cache: 1 repo, [0-9.]+ KiB, oldest just now, newest just now", result.stderr)


def test_cache_status_table_shows_readable_size_and_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    source = _source_repo(tmp_path, "api", {"README.md": "hello\n"})
    _populate_cache(tmp_path, [_repo("acme/api", source)])

    result = CliInvoker().invoke(app, ["cache", "status"])

    assert result.exit_code == 0, result.output
    assert "KiB" in result.stdout
    assert "just now" in result.stdout
    assert "disk_bytes" not in result.stdout


def test_cache_sync_warms_the_corpus_without_a_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    source = _source_repo(tmp_path, "api", {"README.md": "hello\n"})
    listed = [{**_repo("acme/api", source), "pushed_at": "2026-07-01T00:00:00Z"}]

    def sync(*extra: str) -> list[dict[str, object]]:
        with respx.mock(base_url="https://api.github.com") as mock:
            mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=listed))
            result = CliInvoker().invoke(
                app, ["cache", "sync", "--org", "acme", "--format", "json", *extra]
            )
        assert result.exit_code == 0, result.output
        assert result.stderr.splitlines()[-1].startswith("sync: 1 ")
        rows: list[dict[str, object]] = json.loads(result.stdout)
        return rows

    [first] = sync()
    [second] = sync()
    [forced] = sync("--refresh")

    assert list(first) == ["repo", "fetched_at", "error", "action"]
    assert (first["repo"], first["action"]) == ("acme/api", "synced")
    assert second["action"] == "skipped"
    assert forced["action"] == "synced"
    status = CliInvoker().invoke(app, ["cache", "status", "--format", "json"])
    assert [row["repo"] for row in json.loads(status.stdout)] == ["acme/api"]


def test_cache_sync_skips_fetch_when_github_reports_no_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _write_config(tmp_path)
    cfg.write_text(cfg.read_text() + "      sweep:\n        max_age_seconds: 0\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    source = _source_repo(tmp_path, "api", {"README.md": "hello\n"})
    listed = [{**_repo("acme/api", source), "pushed_at": "2026-07-01T00:00:00Z"}]
    actions = []
    for _ in range(2):
        with respx.mock(base_url="https://api.github.com") as mock:
            mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=listed))
            result = CliInvoker().invoke(app, ["cache", "sync", "--org", "acme", "-f", "json"])
        assert result.exit_code == 0, result.output
        actions.append(json.loads(result.stdout)[0]["action"])

    assert actions == ["synced", "unchanged"]


def test_cache_sync_failure_exits_1_and_names_the_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    missing = tmp_path / "missing"

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/repos").mock(
            return_value=httpx.Response(200, json=[_repo("acme/gone", missing)])
        )
        result = CliInvoker().invoke(app, ["cache", "sync", "--org", "acme", "-f", "json"])

    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)[0]["action"] == "failed"
    assert "error: acme/gone:" in result.stderr
    assert "sync: 1 failed" in result.stderr


def test_cache_sync_requires_a_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    result = CliInvoker().invoke(app, ["cache", "sync"])

    assert result.exit_code == 2, result.output
    assert "cache sync requires --org, --team, --repo, or --stdin" in result.stderr


def test_cache_prune_removes_departed_repos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    api = _source_repo(tmp_path, "api", {"README.md": "hello\n"})
    worker = _source_repo(tmp_path, "worker", {"README.md": "hello\n"})
    _populate_cache(tmp_path, [_repo("acme/api", api), _repo("acme/worker", worker)])

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/repos").mock(
            return_value=httpx.Response(200, json=[_repo("acme/api", api)])
        )
        rejected = CliInvoker().invoke(
            app,
            ["cache", "prune", "--org", "acme", "--format", "json"],
        )
    listed_after_reject = CliInvoker().invoke(app, ["cache", "status", "--format", "json"])

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/repos").mock(
            return_value=httpx.Response(200, json=[_repo("acme/api", api)])
        )
        pruned = CliInvoker().invoke(
            app,
            ["cache", "prune", "--org", "acme", "--yes", "--format", "json"],
        )
    listed_after_prune = CliInvoker().invoke(app, ["cache", "status", "--format", "json"])

    assert rejected.exit_code != 0
    assert {row["repo"] for row in json.loads(listed_after_reject.stdout)} == {
        "acme/api",
        "acme/worker",
    }
    assert pruned.exit_code == 0, pruned.output
    assert [row["repo"] for row in json.loads(pruned.stdout)] == ["acme/worker"]
    assert [row["repo"] for row in json.loads(listed_after_prune.stdout)] == ["acme/api"]


def test_cache_prune_has_no_team_option(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    source = _source_repo(tmp_path, "api", {"README.md": "hello\n"})
    _populate_cache(tmp_path, [_repo("acme/api", source)])

    result = CliInvoker().invoke(
        app,
        ["cache", "prune", "--team", "acme/backend", "--yes", "--format", "json"],
    )
    listed = CliInvoker().invoke(app, ["cache", "status", "--format", "json"])

    assert result.exit_code != 0
    assert "--team" in result.output
    assert [row["repo"] for row in json.loads(listed.stdout)] == ["acme/api"]


def test_cache_delete_all_with_org_only_removes_that_org(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    api = _source_repo(tmp_path, "api", {"README.md": "hello\n"})
    tool = _source_repo(tmp_path, "tool", {"README.md": "hello\n"})
    _populate_cache(tmp_path, [_repo("acme/api", api)])
    _populate_cache(tmp_path, [_repo("other/tool", tool)], org="other")

    cleaned = CliInvoker().invoke(
        app, ["cache", "delete", "--all", "--org", "ACME", "--yes", "--format", "json"]
    )
    listed = CliInvoker().invoke(app, ["cache", "status", "--format", "json"])

    assert cleaned.exit_code == 0, cleaned.output
    assert [row["repo"] for row in json.loads(cleaned.stdout)] == ["acme/api"]
    assert [row["repo"] for row in json.loads(listed.stdout)] == ["other/tool"]


def test_cache_delete_repo_with_org_filters_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    api = _source_repo(tmp_path, "api", {"README.md": "hello\n"})
    _populate_cache(tmp_path, [_repo("acme/api", api)])

    cleaned = CliInvoker().invoke(
        app,
        ["cache", "delete", "acme/api", "--org", "other", "--yes", "--format", "json"],
    )
    listed = CliInvoker().invoke(app, ["cache", "status", "--format", "json"])

    assert cleaned.exit_code == 0, cleaned.output
    assert json.loads(cleaned.stdout) == []
    assert [row["repo"] for row in json.loads(listed.stdout)] == ["acme/api"]


def test_cache_clean_requires_exactly_one_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    missing = CliInvoker().invoke(app, ["cache", "clean", "--format", "json"])
    combined = CliInvoker().invoke(
        app,
        ["cache", "clean", "--repo", "acme/api", "--all", "--yes", "--format", "json"],
    )

    assert missing.exit_code == 2
    assert "requires exactly one" in missing.output
    assert combined.exit_code == 2
    assert "requires exactly one" in combined.output


def test_cache_clean_still_works_and_warns_it_is_deprecated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    source = _source_repo(tmp_path, "api", {"README.md": "hello\n"})
    _populate_cache(tmp_path, [_repo("acme/api", source)])

    cleaned = CliInvoker().invoke(
        app, ["cache", "clean", "--repo", "acme/api", "--yes", "--format", "json"]
    )

    assert cleaned.exit_code == 0, cleaned.output
    assert [row["repo"] for row in json.loads(cleaned.stdout)] == ["acme/api"]
    assert "warning: `cache clean` is deprecated" in cleaned.stderr


def test_cache_delete_and_prune_selection_errors_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    missing = CliInvoker().invoke(app, ["cache", "delete"])
    combined = CliInvoker().invoke(app, ["cache", "delete", "acme/api", "--all", "--yes"])
    no_org = CliInvoker().invoke(app, ["cache", "prune", "--yes"])

    assert missing.exit_code == 2
    assert "cache delete requires REPO arguments or --all" in missing.stderr
    assert combined.exit_code == 2
    assert "not both" in combined.stderr
    assert no_org.exit_code == 2
    assert "cache prune requires --org" in no_org.stderr


def test_cache_delete_dry_run_lists_without_deleting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    source = _source_repo(tmp_path, "api", {"README.md": "hello\n"})
    _populate_cache(tmp_path, [_repo("acme/api", source)])

    planned = CliInvoker().invoke(
        app, ["cache", "delete", "--all", "--dry-run", "--yes", "--format", "json"]
    )
    listed = CliInvoker().invoke(app, ["cache", "status", "--format", "json"])

    assert planned.exit_code == 0, planned.output
    assert [row["repo"] for row in json.loads(planned.stdout)] == ["acme/api"]
    assert [row["repo"] for row in json.loads(listed.stdout)] == ["acme/api"]


def test_cache_delete_all_requires_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    source = _source_repo(tmp_path, "api", {"README.md": "hello\n"})
    _populate_cache(tmp_path, [_repo("acme/api", source)])

    result = CliInvoker().invoke(app, ["cache", "delete", "--all", "--format", "json"])
    listed = CliInvoker().invoke(app, ["cache", "status", "--format", "json"])

    assert result.exit_code != 0
    assert "requires --yes" in result.output
    assert [row["repo"] for row in json.loads(listed.stdout)] == ["acme/api"]


def test_cache_delete_repo_conforms_to_destructive_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    source = _source_repo(tmp_path, "api", {"README.md": "hello\n"})
    _populate_cache(tmp_path, [_repo("acme/api", source)])

    def _corpus_still_has_repo() -> None:
        listed = CliInvoker().invoke(app, ["cache", "status", "--format", "json"])
        assert listed.exit_code == 0, listed.output
        assert [row["repo"] for row in json.loads(listed.stdout)] == ["acme/api"]

    assert_destructive_contract(
        app,
        ["cache", "delete", "acme/api", "--format", "json"],
        assert_unchanged=_corpus_still_has_repo,
    )


def test_cache_delete_all_yes_removes_every_cached_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    api = _source_repo(tmp_path, "api", {"README.md": "hello\n"})
    worker = _source_repo(tmp_path, "worker", {"README.md": "hello\n"})
    _populate_cache(tmp_path, [_repo("acme/api", api), _repo("acme/worker", worker)])

    cleaned = CliInvoker().invoke(app, ["cache", "delete", "--all", "--yes", "--format", "json"])
    listed = CliInvoker().invoke(app, ["cache", "status", "--format", "json"])

    assert cleaned.exit_code == 0, cleaned.output
    assert {row["repo"] for row in json.loads(cleaned.stdout)} == {"acme/api", "acme/worker"}
    assert listed.stdout == "[]\n"


def test_cache_worktree_materializes_cached_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    source = _source_repo(tmp_path, "api", {"README.md": "hello\n"})
    _populate_cache(tmp_path, [_repo("acme/api", source)])

    result = CliInvoker().invoke(app, ["cache", "worktree", "acme/api", "--format", "json"])

    assert result.exit_code == 0, result.output
    row = json.loads(result.stdout)
    assert row["repo"] == "acme/api"
    assert (Path(row["path"]) / "README.md").is_file()


def test_cache_delete_repo_matches_case_insensitively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    api = _source_repo(tmp_path, "api", {"README.md": "hello\n"})
    _populate_cache(tmp_path, [_repo("acme/api", api)])

    cleaned = CliInvoker().invoke(app, ["cache", "delete", "ACME/Api", "--yes", "--format", "json"])

    assert cleaned.exit_code == 0, cleaned.output
    assert [row["repo"] for row in json.loads(cleaned.stdout)] == ["acme/api"]
