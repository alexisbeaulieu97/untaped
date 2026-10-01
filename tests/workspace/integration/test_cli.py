"""The workspace CLI end to end: real git, isolated config and state."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from untaped import quiet
from untaped.capabilities.workspace.cli import app
from untaped.testing import CliInvoker, CliResult
from workspace.conftest import commit_in

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("workspace_env")]
run = CliInvoker().invoke


def _rows(result: CliResult) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = json.loads(result.stdout)
    return rows


@pytest.fixture
def quiet_mode() -> Iterator[None]:
    token = quiet.enable()
    yield
    quiet.reset(token)


def test_create_list_path(make_upstream: Callable[..., Path], workspace_env: Path) -> None:
    url = str(make_upstream("api"))
    created = run(app, ["create", "J-1", "--repo", url, "--format", "json"])
    assert created.exit_code == 0, created.output
    [row] = _rows(created)
    assert (row["action"], row["target_path"]) == ("created", str(workspace_env / "J-1" / "api"))
    listed = _rows(run(app, ["list", "--format", "json"]))
    assert [r["name"] for r in listed] == ["J-1"]
    assert run(app, ["list", "--archived", "--format", "json"]).stdout.strip() in ("", "[]")
    assert run(app, ["path", "J-1"]).stdout.strip() == str(workspace_env / "J-1")


def test_create_prints_the_path_last_in_table_mode(
    make_upstream: Callable[..., Path], workspace_env: Path
) -> None:
    created = run(app, ["create", "J-1", "--repo", str(make_upstream("api"))])
    assert created.exit_code == 0, created.output
    lines = created.stdout.strip().splitlines()
    assert len(lines) > 1
    assert lines[-1] == str(workspace_env / "J-1")


@pytest.mark.usefixtures("quiet_mode")
def test_create_quiet_prints_only_the_path(
    make_upstream: Callable[..., Path], workspace_env: Path
) -> None:
    created = run(app, ["create", "J-1", "--repo", str(make_upstream("api"))])
    assert created.exit_code == 0, created.output
    assert created.stdout.strip() == str(workspace_env / "J-1")


def test_create_reads_repos_from_stdin_after_repo(make_upstream: Callable[..., Path]) -> None:
    api, web = str(make_upstream("api")), str(make_upstream("web"))
    created = run(
        app, ["create", "J-1", "--stdin", "--repo", api, "--format", "json"], input=web + "\n"
    )
    assert created.exit_code == 0, created.output
    assert [r["dir"] for r in _rows(created)] == ["api", "web"]


def test_create_reads_clone_urls_from_pipe_records(make_upstream: Callable[..., Path]) -> None:
    web = str(make_upstream("web"))
    line = json.dumps({"untaped": "1", "kind": "github.repo", "record": {"clone_url": web}})
    created = run(app, ["create", "J-1", "--stdin", "--format", "json"], input=line + "\n")
    assert created.exit_code == 0, created.output
    assert [r["dir"] for r in _rows(created)] == ["web"]


def test_create_without_repos_is_usage(workspace_env: Path) -> None:
    result = run(app, ["create", "J-1"])
    assert result.exit_code == 2
    assert "--repo" in result.output


def test_create_with_a_typo_leaves_nothing(workspace_env: Path) -> None:
    result = run(app, ["create", "J-1", "--repo", "./nope-not-a-repo"])
    assert result.exit_code in (1, 2, 4), result.output  # 4: no github scope configured in tests
    assert not (workspace_env / "J-1" / "nope-not-a-repo").exists()
    assert run(app, ["list", "--format", "json"]).stdout.strip() in ("", "[]")


def test_status_from_inside_and_check(
    make_upstream: Callable[..., Path], workspace_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(app, ["create", "J-1", "--repo", str(make_upstream("api"))])
    repo = workspace_env / "J-1" / "api"
    monkeypatch.chdir(repo)
    assert run(app, ["status", "--check"]).exit_code == 0
    (repo / "scratch.txt").write_text("x")
    assert run(app, ["status", "--check"]).exit_code == 3


def test_status_all(make_upstream: Callable[..., Path]) -> None:
    run(app, ["create", "J-1", "--repo", str(make_upstream("api"))])
    run(app, ["create", "J-2", "--repo", str(make_upstream("web"))])
    rows = _rows(run(app, ["status", "--all", "--format", "json"]))
    assert [(r["workspace"], r["state"]) for r in rows] == [("J-1", "ok"), ("J-2", "ok")]


def test_status_all_with_a_name_is_usage(make_upstream: Callable[..., Path]) -> None:
    assert run(app, ["status", "J-1", "--all"]).exit_code == 2


def test_outside_a_workspace_is_usage_with_hint(
    workspace_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = run(app, ["path"])
    assert result.exit_code in (1, 2), result.output
    assert "not inside a workspace" in result.stderr


def test_archive_refuses_dirty_then_forces(
    make_upstream: Callable[..., Path], workspace_env: Path
) -> None:
    run(app, ["create", "J-1", "--repo", str(make_upstream("api"))])
    repo = workspace_env / "J-1" / "api"
    commit_in(repo)
    refused = run(app, ["archive", "J-1", "--format", "json"])
    assert refused.exit_code == 1
    assert [r["action"] for r in _rows(refused)] == ["skipped"]
    assert "--force" in refused.stderr
    assert repo.exists()
    dry = run(app, ["archive", "J-1", "--dry-run", "--format", "json"])
    assert dry.exit_code == 0
    assert [r["action"] for r in _rows(dry)] == ["skipped"]
    assert run(app, ["archive", "J-1", "--force"]).exit_code == 2  # no terminal, no --yes
    forced = run(app, ["archive", "J-1", "--force", "--yes", "--format", "json"])
    assert forced.exit_code == 0, forced.output
    assert not repo.exists()
    assert [r["name"] for r in _rows(run(app, ["list", "--archived", "--format", "json"]))] == [
        "J-1"
    ]


def test_archive_clean_workspace(make_upstream: Callable[..., Path], workspace_env: Path) -> None:
    run(app, ["create", "J-1", "--repo", str(make_upstream("api"))])
    dry = run(app, ["archive", "J-1", "--dry-run", "--format", "json"])
    assert [r["action"] for r in _rows(dry)] == ["planned"]
    archived = run(app, ["archive", "J-1", "--format", "json"])
    assert archived.exit_code == 0, archived.output
    assert not (workspace_env / "J-1").exists()
    assert run(app, ["list", "--format", "json"]).stdout.strip() in ("", "[]")


def test_add_a_second_repo(make_upstream: Callable[..., Path], workspace_env: Path) -> None:
    run(app, ["create", "J-1", "--repo", str(make_upstream("api"))])
    added = run(app, ["add", "J-1", "--repo", str(make_upstream("web")), "--format", "json"])
    assert added.exit_code == 0, added.output
    assert (workspace_env / "J-1" / "web" / "README.md").exists()


def test_add_read_only(make_upstream: Callable[..., Path], workspace_env: Path) -> None:
    run(app, ["create", "J-1", "--repo", str(make_upstream("api"))])
    added = run(app, ["add", "J-1", "--read-only", str(make_upstream("web")), "--format", "json"])
    assert added.exit_code == 0, added.output
    [row] = _rows(added)
    assert (row["read_only"], row["branch"]) == (True, None)


def test_same_branch_in_two_workspaces_conflicts(make_upstream: Callable[..., Path]) -> None:
    url = str(make_upstream("api"))
    run(app, ["create", "J-1", "--repo", url, "--branch", "feature/x"])
    second = run(app, ["create", "J-2", "--repo", url, "--branch", "feature/x", "--format", "json"])
    assert second.exit_code == 1
    [row] = _rows(second)
    assert row["action"] == "failed"
    error = row["error"]
    assert isinstance(error, dict)
    assert error["category"] == "conflict"
