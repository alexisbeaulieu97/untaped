"""The workspace CLI end to end: real git, isolated config and state."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from untaped import quiet
from untaped.testing import CliInvoker, CliResult, ScriptedPromptBackend
from untaped_github import api as github_api
from untaped_github.api import RepositoryInventoryItem
from untaped_workspace.cli import app
from untaped_workspace.errors import WorkspaceError
from untaped_workspace.infrastructure import LocalGitWorktrees, StateWorkspaceStore
from workspace.conftest import add_submodule, commit_in, git, init_submodules

pytestmark = pytest.mark.usefixtures("workspace_env")
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


def test_create_without_repos_makes_an_empty_workspace(workspace_env: Path) -> None:
    created = run(app, ["create", "J-1", "--format", "json"])
    assert created.exit_code == 0, created.output
    assert _rows(created) == []
    assert (workspace_env / "J-1").is_dir()
    record = StateWorkspaceStore().get("J-1")
    assert record is not None and not record.repos


def test_add_without_repos_is_usage() -> None:
    assert run(app, ["create", "J-1"]).exit_code == 0
    result = run(app, ["add", "J-1"])
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
    assert result.exit_code == 2, result.output
    assert "not inside a workspace" in result.stderr
    assert "hint:" in result.stderr


def test_unknown_name_is_not_found(workspace_env: Path) -> None:
    assert run(app, ["path", "nope"]).exit_code == 1


def test_duplicate_create_hint(make_upstream: Callable[..., Path]) -> None:
    url = str(make_upstream("api"))
    run(app, ["create", "J-1", "--repo", url])
    again = run(app, ["create", "J-1", "--repo", url])
    assert again.exit_code == 1
    assert "hint: run `untaped workspace add J-1 --repo REPO`" in again.stderr
    assert "hint: hint:" not in again.stderr


def test_a_repo_missing_from_the_store_blocks_archive_until_confirmed(
    make_upstream: Callable[..., Path], workspace_env: Path, store_root: Path
) -> None:
    run(app, ["create", "J-1", "--repo", str(make_upstream("api"))])
    work = workspace_env / "J-1" / "api" / "scratch.txt"
    work.write_text("precious")
    shutil.rmtree(store_root)
    assert run(app, ["status", "J-1", "--check"]).exit_code == 3
    refused = run(app, ["archive", "J-1"])
    assert refused.exit_code == 1
    assert "--force" in refused.stderr
    assert run(app, ["archive", "J-1", "--force"]).exit_code == 2  # no terminal, no --yes
    assert work.exists()
    forced = run(app, ["archive", "J-1", "--force", "--yes"])
    assert forced.exit_code == 0, forced.output
    assert not work.exists()


def test_status_fetch_failure_is_per_repo(make_upstream: Callable[..., Path]) -> None:
    api, web = make_upstream("api"), make_upstream("web")
    run(app, ["create", "J-1", "--repo", str(api), "--repo", str(web)])
    api.rename(api.with_name("moved.git"))
    result = run(app, ["status", "J-1", "--fetch", "--format", "json"])
    assert result.exit_code == 1, result.output
    rows = _rows(result)
    assert [(r["dir"], r["state"]) for r in rows] == [("api", "ok"), ("web", "ok")]
    assert str(rows[0]["detail"]).startswith("fetch failed: ")
    error = rows[0]["error"]
    assert isinstance(error, dict) and error["system"] == "git"
    assert "error" not in rows[1]


class _ProbingBackend(ScriptedPromptBackend):
    """Confirms; first tries the workspace lock, as a concurrent ``add`` would."""

    def __init__(self, workspaces: Path) -> None:
        super().__init__(confirms=[True])
        self.workspaces = workspaces
        self.busy: str | None = None

    def confirm(self, message: str, *, default: bool) -> bool:
        try:
            with StateWorkspaceStore(workspaces_dir=self.workspaces, lock_timeout=0.1).locked(
                "J-1"
            ):
                pass
        except WorkspaceError as exc:
            self.busy = str(exc)
        return super().confirm(message, default=default)


def test_archive_holds_the_workspace_lock_from_check_to_removal(
    make_upstream: Callable[..., Path], workspace_env: Path
) -> None:
    run(app, ["create", "J-1", "--repo", str(make_upstream("api"))])
    commit_in(workspace_env / "J-1" / "api")
    backend = _ProbingBackend(workspace_env)
    forced = run(app, ["archive", "J-1", "--force"], interactive=True, prompt_backend=backend)
    assert forced.exit_code == 0, forced.output
    assert [method for method, _ in backend.calls] == ["confirm"]
    assert backend.busy == "workspace J-1 is busy (another untaped process)"


def test_archive_refuses_dirty_then_forces(
    make_upstream: Callable[..., Path], workspace_env: Path
) -> None:
    run(app, ["create", "J-1", "--repo", str(make_upstream("api"))])
    repo = workspace_env / "J-1" / "api"
    commit_in(repo)
    refused = run(app, ["archive", "J-1", "--format", "json"])
    assert refused.exit_code == 1
    assert [r["action"] for r in _rows(refused)] == ["skipped"]
    assert "commit and push your changes" in refused.stderr
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


def test_archive_reports_a_repo_that_changed_after_the_check_with_its_hint(
    make_upstream: Callable[..., Path], workspace_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(app, ["create", "J-1", "--repo", str(make_upstream("api"))])
    remove = LocalGitWorktrees.remove

    def edit_then_remove(self: LocalGitWorktrees, url: str, dest: Path, *, force: bool) -> None:
        (dest / "scratch.txt").write_text("late work")  # lands after archive's status check
        remove(self, url, dest, force=force)

    monkeypatch.setattr(LocalGitWorktrees, "remove", edit_then_remove)
    result = run(app, ["archive", "J-1", "--format", "json"])
    repo = workspace_env / "J-1" / "api"
    assert result.exit_code == 1
    assert result.stderr == (
        f"error: J-1/acme/api: {repo}: uncommitted changes; nothing removed\n"
        "hint: the repo changed since the check; run status and archive again\n"
    )


def test_archive_clean_workspace(make_upstream: Callable[..., Path], workspace_env: Path) -> None:
    run(app, ["create", "J-1", "--repo", str(make_upstream("api"))])
    dry = run(app, ["archive", "J-1", "--dry-run", "--format", "json"])
    assert [r["action"] for r in _rows(dry)] == ["planned"]
    archived = run(app, ["archive", "J-1", "--format", "json"])
    assert archived.exit_code == 0, archived.output
    assert not (workspace_env / "J-1").exists()
    assert run(app, ["list", "--format", "json"]).stdout.strip() in ("", "[]")


def test_remove_previews_confirms_then_releases(
    make_upstream: Callable[..., Path], workspace_env: Path, store_root: Path
) -> None:
    url = str(make_upstream("api"))
    run(app, ["create", "J-1", "--repo", url])
    dry = run(app, ["remove", "J-1", "--dry-run", "--format", "json"])
    assert dry.exit_code == 0, dry.output
    assert [(r["repo"], r["action"]) for r in _rows(dry)] == [
        ("acme/api", "planned"),
        ("", "planned"),
    ]
    unconfirmed = run(app, ["remove", "J-1"])  # no terminal, no --yes
    assert unconfirmed.exit_code == 2
    assert "remove requires --yes when not interactive" in unconfirmed.stderr
    assert (workspace_env / "J-1" / "api").exists()

    removed = run(app, ["remove", "J-1", "--yes", "--format", "json"])

    assert removed.exit_code == 0, removed.output
    assert [(r["repo"], r["action"]) for r in _rows(removed)] == [
        ("acme/api", "removed"),
        ("", "removed"),
    ]
    assert not (workspace_env / "J-1").exists()
    assert list(store_root.rglob("*.git")) == []
    assert run(app, ["list", "--archived", "--format", "json"]).stdout.strip() in ("", "[]")


def test_remove_refuses_work_it_would_lose(
    make_upstream: Callable[..., Path], workspace_env: Path
) -> None:
    run(app, ["create", "J-1", "--repo", str(make_upstream("api"))])
    (workspace_env / "J-1" / "api" / "scratch.txt").write_text("precious")
    refused = run(app, ["remove", "J-1", "--yes", "--format", "json"])
    assert refused.exit_code == 1
    assert [r["action"] for r in _rows(refused)] == ["skipped", "planned"]
    assert "error: 1 repo would lose work; nothing removed" in refused.stderr
    assert "hint: commit and push your changes" in refused.stderr
    assert (workspace_env / "J-1" / "api" / "scratch.txt").exists()


def test_remove_of_an_unknown_workspace_is_not_found(workspace_env: Path) -> None:
    result = run(app, ["remove", "nope", "--yes"])
    assert result.exit_code == 1, result.output
    assert result.stderr == "error: workspace not found: 'nope'; known: none\n"


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


def test_read_only_commit_blocks_archive(
    make_upstream: Callable[..., Path], workspace_env: Path
) -> None:
    run(app, ["create", "J-1", "--read-only", str(make_upstream("api"))])
    repo = workspace_env / "J-1" / "api"
    commit_in(repo)
    assert run(app, ["status", "J-1", "--check"]).exit_code == 3
    refused = run(app, ["archive", "J-1", "--format", "json"])
    assert refused.exit_code == 1, refused.output
    [row] = _rows(refused)
    assert (row["action"], row["detail"]) == ("skipped", "1 commit not pushed")
    assert (repo / "change.txt").exists()


def test_unreadable_worktree_is_an_error_row_and_force_archives(
    make_upstream: Callable[..., Path], workspace_env: Path, store_root: Path
) -> None:
    url = str(make_upstream("api"))
    run(app, ["create", "J-1", "--repo", url])
    store_root.rename(store_root.with_name("store-moved"))
    assert run(app, ["create", "J-2", "--repo", url]).exit_code == 0  # a fresh store repo
    status = run(app, ["status", "--all", "--format", "json"])
    assert status.exit_code == 1, status.output
    rows = _rows(status)
    assert [(r["workspace"], r["state"]) for r in rows] == [("J-1", "error"), ("J-2", "ok")]
    [blocker] = rows[0]["blockers"]  # type: ignore[misc]
    assert str(blocker).startswith("git state unreadable: ")
    error = rows[0]["error"]
    assert isinstance(error, dict) and (error["category"], error["system"]) == ("failed", "git")
    refused = run(app, ["archive", "J-1"])
    assert refused.exit_code == 1
    assert "check the repo by hand, then pass --force" in refused.stderr
    forced = run(app, ["archive", "J-1", "--force", "--yes", "--format", "json"])
    assert forced.exit_code == 0, forced.output
    assert not (workspace_env / "J-1").exists()


def test_status_survives_a_base_deleted_on_origin(
    make_upstream: Callable[..., Path], workspace_env: Path
) -> None:
    upstream = make_upstream("api", branches=("dev",))
    run(app, ["create", "J-1", "--repo", str(upstream), "--base", "dev"])
    git(upstream, "branch", "-D", "dev")
    result = run(app, ["status", "J-1", "--fetch", "--check", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert [r["state"] for r in _rows(result)] == ["ok"]


def test_submodules_block_archive_until_forced(
    make_upstream: Callable[..., Path], workspace_env: Path
) -> None:
    upstream = make_upstream("api")
    add_submodule(upstream, make_upstream("lib"))
    run(app, ["create", "J-1", "--repo", str(upstream)])
    repo = workspace_env / "J-1" / "api"
    init_submodules(repo)
    checked = run(app, ["status", "J-1", "--check", "--format", "json"])
    assert checked.exit_code == 3
    [row] = _rows(checked)
    assert row["blockers"] == ["submodules: archive cannot verify or remove them safely"]
    forced = run(app, ["archive", "J-1", "--force", "--yes"])
    assert forced.exit_code == 0, forced.output
    assert not repo.exists()


def test_stash_hint_protects_other_workspaces_stashes(
    make_upstream: Callable[..., Path], workspace_env: Path
) -> None:
    run(app, ["create", "J-1", "--repo", str(make_upstream("api"))])
    repo = workspace_env / "J-1" / "api"
    for key, value in (("user.email", "t@t"), ("user.name", "t")):
        git(repo, "config", key, value)
    (repo / "README.md").write_text("changed")
    git(repo, "stash", "push", "-q")
    refused = run(app, ["archive", "J-1"])
    assert refused.exit_code == 1
    assert "pop or drop the stashes you made on J-1" in refused.stderr
    assert "never drop those" in refused.stderr


def test_archive_with_leftover_files_skips_the_workspace_row(
    make_upstream: Callable[..., Path], workspace_env: Path
) -> None:
    run(app, ["create", "J-1", "--repo", str(make_upstream("api"))])
    (workspace_env / "J-1" / "notes.md").write_text("keep")
    archived = run(app, ["archive", "J-1", "--format", "json"])
    assert archived.exit_code == 0, archived.output
    summary = _rows(archived)[-1]
    assert (summary["repo"], summary["action"]) == ("", "skipped")
    assert (workspace_env / "J-1" / "notes.md").exists()


def _inventory(monkeypatch: pytest.MonkeyPatch, *items: RepositoryInventoryItem) -> None:
    monkeypatch.setattr(
        github_api, "repo_inventory", lambda **_: SimpleNamespace(repos=list(items))
    )


def test_stdin_records_resolve_their_full_name_through_the_inventory(
    make_upstream: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    web = str(make_upstream("web", branches=("dev",)))
    _inventory(
        monkeypatch,
        RepositoryInventoryItem(full_name="acme/web", clone_url=web, default_branch="dev"),
    )
    record = {"repo": "acme/web", "clone_url": "https://example.invalid/acme/web.git"}
    line = json.dumps({"untaped": "1", "kind": "github.repo", "record": record})
    created = run(app, ["create", "J-1", "--stdin", "--format", "json"], input=line + "\n")
    assert created.exit_code == 0, created.output
    [row] = _rows(created)
    assert (row["repo"], row["base"]) == ("acme/web", "dev")


def test_stdin_records_outside_the_inventory_use_their_clone_url(
    make_upstream: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    web = str(make_upstream("web"))
    _inventory(monkeypatch)
    line = json.dumps(
        {"untaped": "1", "kind": "github.repo", "record": {"repo": "acme/web", "clone_url": web}}
    )
    created = run(app, ["create", "J-1", "--stdin", "--format", "json"], input=line + "\n")
    assert created.exit_code == 0, created.output
    assert [r["dir"] for r in _rows(created)] == ["web"]


def test_base_applies_to_read_only_repos(
    make_upstream: Callable[..., Path], workspace_env: Path
) -> None:
    url = str(make_upstream("api", branches=("dev",)))
    created = run(app, ["create", "J-1", "--read-only", url, "--base", "dev", "--format", "json"])
    assert created.exit_code == 0, created.output
    [row] = _rows(created)
    assert (row["base"], row["detail"]) == ("dev", "read-only at origin/dev")


def test_create_refuses_an_old_workspace_directory(
    make_upstream: Callable[..., Path], workspace_env: Path
) -> None:
    old = workspace_env / "J-1"
    old.mkdir(parents=True)
    (old / "untaped.yml").write_text("an old workspace")
    refused = run(app, ["create", "J-1", "--repo", str(make_upstream("api"))])
    assert refused.exit_code == 1, refused.output
    assert "move it aside or pick another name" in refused.stderr
    assert run(app, ["list", "--format", "json"]).stdout.strip() in ("", "[]")
