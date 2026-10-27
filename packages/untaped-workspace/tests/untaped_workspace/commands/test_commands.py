"""The workspace CLI end to end: real git, isolated config and state."""

from __future__ import annotations

import contextvars
import json
import shutil
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from untaped.sdk import Failure
from untaped.sdk.testing import Ran, ScriptedTerminal
from untaped.sdk.testing.git import GitRemote
from untaped_workspace.domain.naming import workspace_key
from untaped_workspace.infrastructure import LocalGitWorktrees, StateWorkspaceStore
from untaped_workspace.infrastructure.state_store import CHECKOUTS
from workspace_test_support import add_submodule, commit_in, err, git, init_submodules, run


def _rows(result: Ran) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = json.loads(result.stdout)
    return rows


def test_create_list_path(make_upstream: Callable[..., GitRemote], workspace_env: Path) -> None:
    url = make_upstream("api").url
    created = run(["create", "J-1", "--repo", url, "--format", "json"])
    assert created.exit == 0, err(created)
    [row] = _rows(created)
    assert (row["action"], row["target_path"]) == ("created", str(workspace_env / "J-1" / "api"))
    listed = _rows(run(["list", "--format", "json"]))
    assert [r["name"] for r in listed] == ["J-1"]
    assert run(["list", "--archived", "--format", "json"]).stdout.strip() in ("", "[]")
    assert run(["path", "J-1"]).stdout.strip() == str(workspace_env / "J-1")


def test_create_prints_the_path_last_in_table_mode(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    created = run(["create", "J-1", "--repo", make_upstream("api").url])
    assert created.exit == 0, err(created)
    lines = created.stdout.strip().splitlines()
    assert len(lines) > 1
    assert lines[-1] == str(workspace_env / "J-1")


def test_create_quiet_prints_only_the_path(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    created = run(["create", "J-1", "--repo", make_upstream("api").url], quiet=True)
    assert created.exit == 0, err(created)
    assert created.stdout.strip() == str(workspace_env / "J-1")


def test_create_reads_repos_from_stdin_after_repo(make_upstream: Callable[..., GitRemote]) -> None:
    api, web = make_upstream("api").url, make_upstream("web").url
    created = run(["create", "J-1", "--stdin", "--repo", api, "--format", "json"], input=web + "\n")
    assert created.exit == 0, err(created)
    assert [r["dir"] for r in _rows(created)] == ["api", "web"]


def test_create_without_repos_makes_an_empty_workspace(workspace_env: Path) -> None:
    created = run(["create", "J-1", "--format", "json"])
    assert created.exit == 0, err(created)
    assert _rows(created) == []
    assert (workspace_env / "J-1").is_dir()
    record = StateWorkspaceStore().get("J-1")
    assert record is not None and not record.repos


def test_a_decomposed_name_finds_the_workspace_create_stored(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    url = make_upstream("api").url
    assert run(["create", "cafe\u0301", "--repo", url]).exit == 0  # macOS's spelling
    assert run(["path", "cafe\u0301"]).stdout.strip() == str(workspace_env / "caf\u00e9")
    dry = run(["remove", "cafe\u0301", "--dry-run", "--format", "json"])
    assert [(r["workspace"], r["repo"]) for r in _rows(dry)] == [
        ("caf\u00e9", "acme/api"),
        ("caf\u00e9", ""),
    ]
    assert not any("used by" in r["detail"] for r in _rows(dry))  # its own record isn't a holder
    assert run(["archive", "cafe\u0301", "--yes"]).exit == 0
    assert run(["remove", "cafe\u0301", "--yes"]).exit == 0  # archived only
    assert not StateWorkspaceStore().archived()


def test_add_without_repos_is_usage() -> None:
    assert run(["create", "J-1"]).exit == 0
    result = run(["add", "J-1"])
    assert result.exit == 2
    assert "--repo" in err(result)


def test_create_with_a_typo_leaves_nothing(workspace_env: Path) -> None:
    result = run(["create", "J-1", "--repo", "./nope-not-a-repo"])
    assert result.exit in (1, 2, 4), err(result)  # 4: no github scope configured in tests
    assert not (workspace_env / "J-1" / "nope-not-a-repo").exists()
    assert run(["list", "--format", "json"]).stdout.strip() in ("", "[]")


def test_status_from_inside_and_check(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(["create", "J-1", "--repo", make_upstream("api").url])
    repo = workspace_env / "J-1" / "api"
    monkeypatch.chdir(repo)
    assert run(["status", "--check"]).exit == 0
    (repo / "scratch.txt").write_text("x")
    assert run(["status", "--check"]).exit == 3


def test_status_all(make_upstream: Callable[..., GitRemote]) -> None:
    run(["create", "J-1", "--repo", make_upstream("api").url])
    run(["create", "J-2", "--repo", make_upstream("web").url])
    rows = _rows(run(["status", "--all", "--format", "json"]))
    assert [(r["workspace"], r["state"]) for r in rows] == [("J-1", "ok"), ("J-2", "ok")]


def test_status_all_with_a_name_is_usage(make_upstream: Callable[..., GitRemote]) -> None:
    assert run(["status", "J-1", "--all"]).exit == 2


def test_outside_a_workspace_is_usage_with_hint(
    workspace_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = run(["path"])
    assert result.exit == 2, err(result)
    assert "not inside a workspace" in err(result)
    assert "hint:" in err(result)


def test_unknown_name_is_not_found(workspace_env: Path) -> None:
    assert run(["path", "nope"]).exit == 1


def test_duplicate_create_hint(make_upstream: Callable[..., GitRemote]) -> None:
    url = make_upstream("api").url
    run(["create", "J-1", "--repo", url])
    again = run(["create", "J-1", "--repo", url])
    assert again.exit == 1
    assert "hint: run `untaped workspace add J-1 --repo REPO`" in err(again)
    assert "hint: hint:" not in err(again)


def test_a_repo_missing_from_the_store_blocks_archive_until_confirmed(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, store_root: Path
) -> None:
    run(["create", "J-1", "--repo", make_upstream("api").url])
    work = workspace_env / "J-1" / "api" / "scratch.txt"
    work.write_text("precious")
    shutil.rmtree(store_root)
    assert run(["status", "J-1", "--check"]).exit == 3
    refused = run(["archive", "J-1"])
    assert refused.exit == 1
    assert "--force" in err(refused)
    assert run(["archive", "J-1", "--force"]).exit == 2  # no terminal, no --yes
    assert work.exists()
    forced = run(["archive", "J-1", "--force", "--yes"])
    assert forced.exit == 0, err(forced)
    assert not work.exists()


def test_status_fetch_failure_is_per_repo(make_upstream: Callable[..., GitRemote]) -> None:
    api, web = make_upstream("api"), make_upstream("web")
    run(["create", "J-1", "--repo", api.url, "--repo", web.url])
    api.path.rename(api.path.with_name("moved.git"))
    result = run(["status", "J-1", "--fetch", "--format", "json"])
    assert result.exit == 1, err(result)
    rows = _rows(result)
    assert [(r["dir"], r["state"]) for r in rows] == [("api", "ok"), ("web", "ok")]
    assert str(rows[0]["detail"]).startswith("fetch failed: ")
    error = rows[0]["error"]
    assert isinstance(error, dict) and error["system"] == "git"
    assert "error" not in rows[1]


def test_status_fetch_refuses_an_unknown_column_before_fetching(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    api = make_upstream("api")
    run(["create", "J-1", "--repo", api.url])
    repo = workspace_env / "J-1" / "api"
    before = git(repo, "for-each-ref")
    api.commit("new.txt", "upstream moved\n")

    refused = run(["status", "J-1", "--fetch", "--format", "json", "-c", "dir,nope"])

    assert refused.exit == 2, err(refused)
    assert "unknown column 'nope'" in err(refused)
    assert git(repo, "for-each-ref") == before
    assert run(["status", "J-1", "--fetch"]).exit == 0
    assert git(repo, "for-each-ref") != before  # the probe sees a fetch


def test_archive_holds_the_workspace_lock_from_check_to_removal(
    make_upstream: Callable[..., GitRemote],
    workspace_env: Path,
    terminal: ScriptedTerminal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run(["create", "J-1", "--repo", make_upstream("api").url])
    commit_in(workspace_env / "J-1" / "api")
    busy: list[bool] = []

    def probe() -> None:  # another thread, as a concurrent ``add``: in one it is re-entrant
        with CHECKOUTS.try_locked(workspace_key("J-1")) as held:
            busy.append(not held)

    answer = terminal.answers(True).confirm

    def confirm(message: str, *, default: bool) -> bool:
        worker = threading.Thread(target=contextvars.copy_context().run, args=(probe,))
        worker.start()
        worker.join()
        return answer(message, default=default)

    monkeypatch.setattr(terminal, "confirm", confirm)
    forced = run(["archive", "J-1", "--force"])
    assert forced.exit == 0, err(forced)
    assert [kind for kind, _ in terminal.asked] == ["confirm"]
    assert busy == [True]


def test_archive_refuses_dirty_then_forces(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    run(["create", "J-1", "--repo", make_upstream("api").url])
    repo = workspace_env / "J-1" / "api"
    commit_in(repo)
    refused = run(["archive", "J-1", "--format", "json"])
    assert refused.exit == 1
    assert [r["action"] for r in _rows(refused)] == ["skipped"]
    assert "commit and push your changes" in err(refused)
    assert repo.exists()
    dry = run(["archive", "J-1", "--dry-run", "--format", "json"])
    assert dry.exit == 0
    assert [r["action"] for r in _rows(dry)] == ["skipped"]
    assert run(["archive", "J-1", "--force"]).exit == 2  # no terminal, no --yes
    forced = run(["archive", "J-1", "--force", "--yes", "--format", "json"])
    assert forced.exit == 0, err(forced)
    assert not repo.exists()
    assert [r["name"] for r in _rows(run(["list", "--archived", "--format", "json"]))] == ["J-1"]


def test_archive_reports_a_repo_that_changed_after_the_check_with_its_hint(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(["create", "J-1", "--repo", make_upstream("api").url])
    remove = LocalGitWorktrees.remove

    def edit_then_remove(
        self: LocalGitWorktrees, url: str, dest: Path, *, base: str, force: bool
    ) -> None:
        (dest / "scratch.txt").write_text("late work")  # lands after archive's status check
        remove(self, url, dest, base=base, force=force)

    monkeypatch.setattr(LocalGitWorktrees, "remove", edit_then_remove)
    result = run(["archive", "J-1", "--format", "json"])
    repo = workspace_env / "J-1" / "api"
    assert result.exit == 1
    [failure] = [each for each in result.diagnostics if isinstance(each, Failure)]
    assert (failure.item, failure.message, failure.hint) == (
        "J-1/acme/api",
        f"{repo}: uncommitted changes; nothing removed",
        "the repo changed since the check; run status and archive again",
    )


def test_archive_clean_workspace(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    run(["create", "J-1", "--repo", make_upstream("api").url])
    dry = run(["archive", "J-1", "--dry-run", "--format", "json"])
    assert [r["action"] for r in _rows(dry)] == ["planned"]
    archived = run(["archive", "J-1", "--format", "json"])
    assert archived.exit == 0, err(archived)
    assert not (workspace_env / "J-1").exists()
    assert run(["list", "--format", "json"]).stdout.strip() in ("", "[]")


def test_remove_previews_confirms_then_releases(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, store_root: Path
) -> None:
    url = make_upstream("api").url
    run(["create", "J-1", "--repo", url])
    dry = run(["remove", "J-1", "--dry-run", "--format", "json"])
    assert dry.exit == 0, err(dry)
    assert [(r["repo"], r["action"]) for r in _rows(dry)] == [
        ("acme/api", "planned"),
        ("", "planned"),
    ]
    unconfirmed = run(["remove", "J-1"])  # no terminal, no --yes
    assert unconfirmed.exit == 2
    assert "remove requires --yes when not interactive" in err(unconfirmed)
    assert (workspace_env / "J-1" / "api").exists()

    removed = run(["remove", "J-1", "--yes", "--format", "json"])

    assert removed.exit == 0, err(removed)
    assert [(r["repo"], r["action"]) for r in _rows(removed)] == [
        ("acme/api", "removed"),
        ("", "removed"),
    ]
    assert not (workspace_env / "J-1").exists()
    assert list(store_root.rglob("*.git")) == []
    assert run(["list", "--archived", "--format", "json"]).stdout.strip() in ("", "[]")


def test_remove_refuses_work_it_would_lose(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    run(["create", "J-1", "--repo", make_upstream("api").url])
    (workspace_env / "J-1" / "api" / "scratch.txt").write_text("precious")
    refused = run(["remove", "J-1", "--yes", "--format", "json"])
    assert refused.exit == 1
    assert [r["action"] for r in _rows(refused)] == ["skipped", "planned"]
    [failure] = [each for each in refused.diagnostics if isinstance(each, Failure)]
    assert (failure.message, failure.hint) == (
        "1 repo would lose work; nothing removed",
        "commit and push your changes",
    )
    assert (workspace_env / "J-1" / "api" / "scratch.txt").exists()


def test_remove_of_an_unknown_workspace_is_not_found(workspace_env: Path) -> None:
    result = run(["remove", "nope", "--yes"])
    assert result.exit == 1, err(result)
    assert result.stderr == ["error: workspace not found: 'nope'; known: none"]


def test_add_a_second_repo(make_upstream: Callable[..., GitRemote], workspace_env: Path) -> None:
    run(["create", "J-1", "--repo", make_upstream("api").url])
    added = run(["add", "J-1", "--repo", make_upstream("web").url, "--format", "json"])
    assert added.exit == 0, err(added)
    assert (workspace_env / "J-1" / "web" / "README.md").exists()


def test_add_read_only(make_upstream: Callable[..., GitRemote], workspace_env: Path) -> None:
    run(["create", "J-1", "--repo", make_upstream("api").url])
    added = run(["add", "J-1", "--read-only", make_upstream("web").url, "--format", "json"])
    assert added.exit == 0, err(added)
    [row] = _rows(added)
    assert (row["read_only"], row["branch"]) == (True, None)


def test_same_branch_in_two_workspaces_conflicts(make_upstream: Callable[..., GitRemote]) -> None:
    url = make_upstream("api").url
    run(["create", "J-1", "--repo", url, "--branch", "feature/x"])
    second = run(["create", "J-2", "--repo", url, "--branch", "feature/x", "--format", "json"])
    assert second.exit == 1
    [row] = _rows(second)
    assert row["action"] == "failed"
    error = row["error"]
    assert isinstance(error, dict)
    assert error["category"] == "conflict"


def test_read_only_commit_blocks_archive(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    run(["create", "J-1", "--read-only", make_upstream("api").url])
    repo = workspace_env / "J-1" / "api"
    commit_in(repo)
    assert run(["status", "J-1", "--check"]).exit == 3
    refused = run(["archive", "J-1", "--format", "json"])
    assert refused.exit == 1, err(refused)
    [row] = _rows(refused)
    assert (row["action"], row["detail"]) == ("skipped", "1 commit not pushed")
    assert (repo / "change.txt").exists()


def test_unreadable_worktree_is_an_error_row_and_force_archives(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, store_root: Path
) -> None:
    url = make_upstream("api").url
    run(["create", "J-1", "--repo", url])
    store_root.rename(store_root.with_name("store-moved"))
    assert run(["create", "J-2", "--repo", url]).exit == 0  # a fresh store repo
    status = run(["status", "--all", "--format", "json"])
    assert status.exit == 1, err(status)
    rows = _rows(status)
    assert [(r["workspace"], r["state"]) for r in rows] == [("J-1", "error"), ("J-2", "ok")]
    [blocker] = rows[0]["blockers"]  # type: ignore[misc]
    assert str(blocker).startswith("git state unreadable: ")
    error = rows[0]["error"]
    assert isinstance(error, dict) and (error["category"], error["system"]) == ("failed", "git")
    refused = run(["archive", "J-1"])
    assert refused.exit == 1
    assert "check the repo by hand, then pass --force" in err(refused)
    forced = run(["archive", "J-1", "--force", "--yes", "--format", "json"])
    assert forced.exit == 0, err(forced)
    assert not (workspace_env / "J-1").exists()


def test_status_survives_a_base_deleted_on_origin(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    upstream = make_upstream("api", branches=("dev",))
    run(["create", "J-1", "--repo", upstream.url, "--base", "dev"])
    git(upstream.path, "branch", "-D", "dev")
    result = run(["status", "J-1", "--fetch", "--check", "--format", "json"])
    assert result.exit == 0, err(result)
    assert [r["state"] for r in _rows(result)] == ["ok"]


def test_submodules_block_archive_until_forced(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    upstream = make_upstream("api")
    add_submodule(upstream, make_upstream("lib"))
    run(["create", "J-1", "--repo", upstream.url])
    repo = workspace_env / "J-1" / "api"
    init_submodules(repo)
    checked = run(["status", "J-1", "--check", "--format", "json"])
    assert checked.exit == 3
    [row] = _rows(checked)
    assert row["blockers"] == ["submodules: archive cannot verify or remove them safely"]
    forced = run(["archive", "J-1", "--force", "--yes"])
    assert forced.exit == 0, err(forced)
    assert not repo.exists()


def test_stash_hint_protects_other_workspaces_stashes(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    run(["create", "J-1", "--repo", make_upstream("api").url])
    repo = workspace_env / "J-1" / "api"
    for key, value in (("user.email", "t@t"), ("user.name", "t")):
        git(repo, "config", key, value)
    (repo / "README.md").write_text("changed")
    git(repo, "stash", "push", "-q")
    refused = run(["archive", "J-1"])
    assert refused.exit == 1
    assert "pop or drop the stashes you made on J-1" in err(refused)
    assert "never drop those" in err(refused)


def test_archive_with_leftover_files_skips_the_workspace_row(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    run(["create", "J-1", "--repo", make_upstream("api").url])
    (workspace_env / "J-1" / "notes.md").write_text("keep")
    archived = run(["archive", "J-1", "--format", "json"])
    assert archived.exit == 0, err(archived)
    summary = _rows(archived)[-1]
    assert (summary["repo"], summary["action"]) == ("", "skipped")
    assert (workspace_env / "J-1" / "notes.md").exists()


def test_base_applies_to_read_only_repos(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    url = make_upstream("api", branches=("dev",)).url
    created = run(["create", "J-1", "--read-only", url, "--base", "dev", "--format", "json"])
    assert created.exit == 0, err(created)
    [row] = _rows(created)
    assert (row["base"], row["detail"]) == ("dev", "read-only at origin/dev")


def test_create_refuses_an_old_workspace_directory(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    old = workspace_env / "J-1"
    old.mkdir(parents=True)
    (old / "untaped.yml").write_text("an old workspace")
    refused = run(["create", "J-1", "--repo", make_upstream("api").url])
    assert refused.exit == 1, err(refused)
    assert "move it aside or pick another name" in err(refused)
    assert run(["list", "--format", "json"]).stdout.strip() in ("", "[]")
