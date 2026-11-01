"""A workspace is the git part of a project: made, resumed, moved and removed with it."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from untaped.sdk import home
from untaped.sdk.testing import install
from untaped.sdk.testing.git import GitRemote
from untaped_workspace.domain.models import RepoKey
from untaped_workspace.plugin import WorkspacePlugin
from workspace_test_support import err, git, run, store_repo, stored


def _untaped(*args: str) -> tuple[int, str, str]:
    ran = install(WorkspacePlugin).cli(list(args))
    return int(ran.exit), ran.stdout, err(ran)


def test_a_repo_key_is_one_hold_for_every_spelling() -> None:
    https = RepoKey.model_validate("https://github.com/acme/api.git")
    assert https.root == "github.com/acme/api"
    assert RepoKey.model_validate("git@github.com:acme/api.git") == https
    assert RepoKey.model_validate("github.com/acme/api.git") == https


def test_create_makes_a_project_holding_its_repos(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    created = run(["create", "J-1", "--repo", make_upstream("api").url])
    assert created.exit == 0, err(created)
    record = stored("J-1")
    assert record is not None and record.root == workspace_env / "J-1"
    assert (workspace_env / "J-1" / ".untaped" / "project.json").is_file()
    assert [key.root for key in record.project.holds(_held())] == ["git.example/acme/api"]
    locked = git(workspace_env / "J-1" / "api", "worktree", "list", "--porcelain")
    assert f"locked untaped project {record.project.id}" in locked


def test_an_empty_workspace_takes_repos_later(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    assert run(["create", "J-1", "--empty"]).exit == 0
    added = run(["add", "J-1", "--repo", make_upstream("api").url])
    assert added.exit == 0, err(added)
    assert (workspace_env / "J-1" / "api" / "README.md").is_file()


def test_create_records_the_runs_explicit_profile_pick(workspace_env: Path) -> None:
    config = home().expanduser() / "config.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("format_version: 2\nprofiles:\n  staging: {}\n", encoding="utf-8")
    code, _, stderr = _untaped("--profile", "staging", "workspace", "create", "J-1", "--empty")
    assert code == 0, stderr
    text = (workspace_env / "J-1" / ".untaped" / "config.yml").read_text(encoding="utf-8")
    assert "profile: staging" in text


def test_archive_keeps_the_project_and_create_resumes_it(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    api, web = make_upstream("api").url, make_upstream("web").url
    assert run(["create", "J-1", "--repo", api]).exit == 0
    assert run(["archive", "J-1"]).exit == 0
    assert not (workspace_env / "J-1" / "api").exists()
    listed = json.loads(run(["list", "--archived", "--format", "json"]).stdout)
    assert [row["name"] for row in listed] == ["J-1"]

    resumed = run(["create", "J-1", "--repo", web, "--format", "json"])

    assert resumed.exit == 0, err(resumed)
    assert (workspace_env / "J-1" / "api" / "README.md").is_file()  # checked out again
    assert (workspace_env / "J-1" / "web" / "README.md").is_file()
    record = stored("J-1")
    assert record is not None and not record.archived


def test_a_moved_workspace_works_once_project_move_repairs_it(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, tmp_path: Path
) -> None:
    assert run(["create", "J-1", "--repo", make_upstream("api").url]).exit == 0
    moved = (workspace_env / "J-1").rename(tmp_path / "elsewhere")
    code, _, stderr = _untaped("project", "move", str(workspace_env / "J-1"), str(moved))
    assert code == 0, stderr
    status = run(["status", str(moved), "--format", "json"])
    assert status.exit == 0, err(status)
    assert [row["state"] for row in json.loads(status.stdout)] == ["ok"]
    assert git(moved / "api", "status", "--porcelain") == ""  # the worktree was reconnected


def test_project_remove_releases_the_workspace_s_repos(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, store_root: Path
) -> None:
    url = make_upstream("api").url
    assert run(["create", "J-1", "--repo", url]).exit == 0
    code, _, stderr = _untaped("project", "remove", str(workspace_env / "J-1"), "--yes")
    assert code == 0, stderr
    assert not store_repo(store_root, url).exists()
    assert not (workspace_env / "J-1" / "api").exists()


def _held():  # type: ignore[no-untyped-def]
    from untaped_workspace.infrastructure.project_store import HELD_REPOS

    return HELD_REPOS.items()
